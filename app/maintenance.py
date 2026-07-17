"""Einmalige Datenmigrationen und wiederkehrende Hintergrund-Aufgaben."""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from sqlalchemy import select

from . import backup, config, images
from .database import SessionLocal
from .models import Article, ArticleImage, Sale, StorageLocation
from .services import make_article_no

log = logging.getLogger("warensystem")


def backfill_article_numbers() -> None:
    """Vergibt fehlende Artikelnummern für Bestandsdaten (einmalig beim Start)."""
    db = SessionLocal()
    try:
        missing = db.scalars(select(Article).where(Article.article_no == "")).all()
        for a in missing:
            a.article_no = make_article_no(a.id)
        if missing:
            db.commit()
    finally:
        db.close()




def backfill_storage_locations() -> None:
    """Übernimmt vorhandene Artikel-Lagerplätze einmalig in die verwaltete Liste."""
    db = SessionLocal()
    try:
        existing = {
            (l.area, l.shelf, l.bin) for l in db.scalars(select(StorageLocation)).all()
        }
        seen = set()
        for a in db.scalars(select(Article)).all():
            key = (a.storage_area, a.storage_shelf, a.storage_bin)
            if key == ("", "", "") or key in existing or key in seen:
                continue
            seen.add(key)
            db.add(StorageLocation(area=key[0], shelf=key[1], bin=key[2]))
        if seen:
            db.commit()
    finally:
        db.close()




def migrate_legacy_sales() -> int:
    """Überführt Alt-Verkäufe (Verkaufsdaten am Artikel) einmalig in die Sale-Tabelle.

    Idempotent: Artikel, die bereits einen Verkauf haben, werden übersprungen.
    Verkaufte Alt-Artikel bekommen Bestand 0.
    """
    db = SessionLocal()
    try:
        legacy = db.scalars(select(Article).where(Article.sold_at.is_not(None))).all()
        migrated = 0
        for a in legacy:
            if a.sales:  # bereits migriert
                continue
            db.add(Sale(
                article_id=a.id,
                quantity=1,
                sold_price=a.sold_price,
                unit_purchase_cost=a.purchase_cost,
                fees=a.fees,
                shipping_method=a.shipping_method,
                shipping_cost=a.shipping_cost,
                shipping_payer=a.shipping_payer or "Käufer",
                sale_platform=a.sale_platform,
                buyer_name=a.buyer_name,
                buyer_address=a.buyer_address,
                payment_method=a.payment_method,
                tracking_carrier=a.tracking_carrier,
                tracking_number=a.tracking_number,
                note=a.note,
                order_date=a.order_date,
                shipped_at=a.shipped_at,
                sold_at=a.sold_at,
            ))
            a.quantity = 0  # Einzelstück war verkauft
            migrated += 1
        if migrated:
            db.commit()
            log.info("Migration: %d Alt-Verkäufe in die Verkaufshistorie übernommen.", migrated)
        return migrated
    finally:
        db.close()




def archive_old_sales() -> int:
    """Archiviert ausverkaufte Artikel, deren letzter Verkauf länger zurückliegt.

    Kriterium: Bestand 0 und letzter Verkauf älter als ARCHIVE_AFTER_DAYS.
    Die Verkaufshistorie bleibt erhalten, damit die Statistik stimmt.
    """
    days = config.ARCHIVE_AFTER_DAYS
    if days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    db = SessionLocal()
    try:
        candidates = db.scalars(
            select(Article).where(Article.quantity == 0, Article.status != "Archiviert")
        ).all()
        n = 0
        for a in candidates:
            last = a.last_sold_at
            if last is None:
                continue
            if last.tzinfo is None:            # naive Werte als UTC behandeln
                last = last.replace(tzinfo=timezone.utc)
            if last <= cutoff:
                a.status = "Archiviert"
                n += 1
        if n:
            db.commit()
            log.info("Auto-Archivierung: %d ausverkaufte Artikel archiviert.", n)
        return n
    finally:
        db.close()


def optimize_existing_images() -> tuple[int, int]:
    """Einmalige Nachbearbeitung vorhandener Bilder.

    Verkleinert zu große Originale und erzeugt fehlende Thumbnails.
    Gibt (verkleinert, Thumbnails erzeugt) zurück.
    """
    db = SessionLocal()
    try:
        verkleinert = thumbs = 0
        for img in db.scalars(select(ArticleImage)).all():
            if images.optimize_existing(config.UPLOAD_DIR, img.filename):
                verkleinert += 1
            if images.ensure_thumb(config.UPLOAD_DIR, img.filename):
                thumbs += 1
        if verkleinert or thumbs:
            log.info("Bilder nachbearbeitet: %d verkleinert, %d Thumbnails erzeugt.",
                     verkleinert, thumbs)
        return verkleinert, thumbs
    finally:
        db.close()


async def _image_maintenance():
    """Bestandsbilder einmalig nachziehen (blockiert den Start nicht)."""
    try:
        await asyncio.to_thread(optimize_existing_images)
    except Exception:
        log.exception("Bild-Nachbearbeitung fehlgeschlagen")


async def _archive_loop():
    """Prüft periodisch auf zu archivierende Verkäufe (alle 6 Stunden)."""
    while True:
        try:
            await asyncio.to_thread(archive_old_sales)
        except Exception:  # Loop niemals sterben lassen
            log.exception("Auto-Archivierung fehlgeschlagen")
        await asyncio.sleep(6 * 3600)


async def _backup_loop():
    """Erstellt regelmäßig eine Sicherung (Standard: täglich)."""
    # Nach dem Start kurz warten, damit Migrationen o.ä. durch sind
    await asyncio.sleep(60)
    while True:
        try:
            path = await asyncio.to_thread(backup.write_backup_file)
            log.info("Automatische Sicherung erstellt: %s", path)
        except Exception:
            log.exception("Automatische Sicherung fehlgeschlagen")
        await asyncio.sleep(config.AUTO_BACKUP_HOURS * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(_image_maintenance())]
    if config.ARCHIVE_AFTER_DAYS > 0:
        tasks.append(asyncio.create_task(_archive_loop()))
    if config.AUTO_BACKUP_HOURS > 0:
        tasks.append(asyncio.create_task(_backup_loop()))
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
