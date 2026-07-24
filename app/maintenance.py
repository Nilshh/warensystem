"""Einmalige Datenmigrationen und wiederkehrende Hintergrund-Aufgaben."""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from fastapi import FastAPI
from sqlalchemy import inspect, select, text

from . import backup, carriers, config, images
from .database import SessionLocal, engine
from .models import Article, ArticleImage, Sale, StorageLocation, FULFILLMENT_CANCELLED
from . import services
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




# Spalten, die vor Einführung der Verkaufshistorie am Artikel hingen.
# Sie sind nicht mehr Teil des Datenmodells und werden nur hier per SQL gelesen.
_LEGACY_COLUMNS = (
    "sold_price", "fees", "sale_platform", "buyer_name", "buyer_address",
    "payment_method", "tracking_carrier", "tracking_number",
    "order_date", "shipped_at", "sold_at",
)


def _parse_dt(value):
    """SQLite liefert Datumsangaben als Text zurück."""
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def migrate_legacy_sales() -> int:
    """Überführt Alt-Verkäufe (Verkaufsdaten am Artikel) in die Sale-Tabelle.

    Betrifft Datenbanken aus der Zeit vor der Verkaufshistorie — auch solche,
    die über ein altes Backup wieder eingespielt werden. Die Alt-Spalten werden
    bewusst per SQL gelesen, da sie nicht mehr im Modell stehen.

    Idempotent: Artikel, die bereits einen Verkauf haben, werden übersprungen.
    Verkaufte Alt-Artikel bekommen Bestand 0.
    """
    vorhandene = {c["name"] for c in inspect(engine).get_columns("articles")}
    if not {"sold_at", "sold_price"} <= vorhandene:
        return 0            # neue Datenbank ohne Alt-Spalten — nichts zu tun

    spalten = ", ".join(
        ("id", "purchase_cost", "shipping_method", "shipping_cost",
         "shipping_payer", "note") + _LEGACY_COLUMNS
    )
    db = SessionLocal()
    try:
        rows = db.execute(text(
            f"SELECT {spalten} FROM articles "
            "WHERE sold_at IS NOT NULL "
            "  AND id NOT IN (SELECT article_id FROM sales)"
        )).mappings().all()

        for r in rows:
            db.add(Sale(
                article_id=r["id"],
                quantity=1,
                sold_price=r["sold_price"] or 0.0,
                unit_purchase_cost=r["purchase_cost"] or 0.0,
                fees=r["fees"] or 0.0,
                shipping_method=r["shipping_method"] or "",
                shipping_cost=r["shipping_cost"] or 0.0,
                shipping_payer=r["shipping_payer"] or "Käufer",
                sale_platform=r["sale_platform"] or "",
                buyer_name=r["buyer_name"] or "",
                buyer_address=r["buyer_address"] or "",
                payment_method=r["payment_method"] or "",
                tracking_carrier=r["tracking_carrier"] or "",
                tracking_number=r["tracking_number"] or "",
                note=r["note"] or "",
                order_date=_parse_dt(r["order_date"]),
                shipped_at=_parse_dt(r["shipped_at"]),
                sold_at=_parse_dt(r["sold_at"]),
            ))
            # Einzelstück war verkauft
            db.execute(text("UPDATE articles SET quantity = 0 WHERE id = :id"),
                       {"id": r["id"]})
        if rows:
            db.commit()
            log.info("Migration: %d Alt-Verkäufe in die Verkaufshistorie übernommen.",
                     len(rows))
        return len(rows)
    finally:
        db.close()




def backfill_fulfillment() -> None:
    """Setzt bei Bestandsverkäufen einen sinnvollen Abwicklungsstatus.

    Bestehende (historische) Verkäufe gelten als abgeschlossen, damit sie nicht
    in der Aufgabenliste auftauchen. Nur ausgeführt, wenn die Spalte existiert.
    """
    if "fulfillment" not in {c["name"] for c in inspect(engine).get_columns("sales")}:
        return
    db = SessionLocal()
    try:
        offen = db.scalars(
            select(Sale).where((Sale.fulfillment == "") | (Sale.fulfillment.is_(None)))
        ).all()
        for s in offen:
            s.fulfillment = "Abgeschlossen"
        if offen:
            db.commit()
            log.info("Backfill: %d Bestandsverkäufe auf 'Abgeschlossen' gesetzt.", len(offen))
    finally:
        db.close()


def auto_complete_sales() -> int:
    """Schließt zugestellte Verkäufe nach AUTO_COMPLETE_DAYS automatisch ab."""
    days = config.AUTO_COMPLETE_DAYS
    if days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    db = SessionLocal()
    try:
        candidates = db.scalars(
            select(Sale).where(Sale.fulfillment == "Zugestellt")
        ).all()
        n = 0
        for s in candidates:
            geliefert = s.tracking_delivered_at
            if geliefert is None:
                continue
            if geliefert.tzinfo is None:
                geliefert = geliefert.replace(tzinfo=timezone.utc)
            if geliefert <= cutoff:
                s.fulfillment = "Abgeschlossen"
                n += 1
        if n:
            db.commit()
            log.info("Auto-Abschluss: %d zugestellte Verkäufe abgeschlossen.", n)
        return n
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


class TrackingRun(NamedTuple):
    """Ergebnis eines Abfragelaufs."""
    checked: int = 0            # tatsächlich beim Dienstleister angefragt
    updated: int = 0            # davon mit neuem Status
    skipped: int = 0            # übersprungen (kein unterstützter Anbieter o.ä.)
    errors: list[str] = []      # aufgetretene Fehlermeldungen (ohne Dubletten)


def _check_sale(sale: Sale) -> str | None:
    """Fragt eine Sendung ab und überträgt das Ergebnis. Gibt Fehlertext zurück."""
    carrier = carriers.detect(sale.tracking_carrier, sale.shipping_method)
    if not carrier:
        return None                       # z.B. Hermes — bleibt manuell
    try:
        res = carriers.track(carrier, sale.tracking_number)
    except carriers.TrackingError as e:
        log.warning("Sendungsverfolgung %s: %s", sale.tracking_number, e)
        return str(e)

    sale.tracking_status = res.status
    sale.tracking_status_text = res.text
    sale.tracking_checked_at = datetime.now(timezone.utc)
    if res.status == carriers.ZUGESTELLT:
        sale.tracking_delivered_at = res.delivered_at or datetime.now(timezone.utc)
        services.advance_fulfillment(sale, "Zugestellt")   # Abwicklung mitziehen
    return None


def update_tracking() -> TrackingRun:
    """Fragt den Sendungsstatus offener Sendungen ab.

    Nur Verkäufe mit Sendungsnummer, deren Dienstleister unterstützt wird, die
    noch nicht zugestellt und nicht storniert sind. Sehr alte Sendungen werden
    nicht mehr abgefragt (schont das Abfragekontingent).
    """
    if not carriers.is_configured():
        return TrackingRun(errors=["Sendungsverfolgung ist nicht konfiguriert "
                                   "(DHL_API_KEY fehlt)."])

    cutoff = datetime.now(timezone.utc) - timedelta(days=config.TRACKING_MAX_DAYS)
    db = SessionLocal()
    try:
        offen = db.scalars(
            select(Sale).where(
                Sale.tracking_number != "",
                Sale.tracking_status != carriers.ZUGESTELLT,
                Sale.fulfillment != FULFILLMENT_CANCELLED,
            )
        ).all()

        checked = updated = skipped = 0
        errors: list[str] = []
        for s in offen:
            verkauft = s.sold_at
            if verkauft and verkauft.tzinfo is None:
                verkauft = verkauft.replace(tzinfo=timezone.utc)
            if verkauft and verkauft < cutoff:
                skipped += 1
                continue                       # zu alt, nicht weiter verfolgen
            if not carriers.detect(s.tracking_carrier, s.shipping_method):
                skipped += 1
                continue                       # kein unterstützter Anbieter

            checked += 1
            fehler = _check_sale(s)
            if fehler:
                if fehler not in errors:
                    errors.append(fehler)
            else:
                updated += 1

        if updated:
            db.commit()
            log.info("Sendungsverfolgung: %d Sendungen aktualisiert.", updated)
        return TrackingRun(checked, updated, skipped, errors)
    finally:
        db.close()


def refresh_sale_tracking(sale_id: int) -> str | None:
    """Fragt eine einzelne Sendung sofort ab. Gibt einen Hinweistext zurück."""
    if not carriers.is_configured():
        return "Sendungsverfolgung ist nicht konfiguriert (DHL_API_KEY fehlt)."
    db = SessionLocal()
    try:
        sale = db.get(Sale, sale_id)
        if not sale:
            return "Verkauf nicht gefunden."
        if not sale.tracking_number:
            return "Keine Sendungsnummer hinterlegt."
        if not carriers.detect(sale.tracking_carrier, sale.shipping_method):
            return (f"'{sale.tracking_carrier or sale.shipping_method or '—'}' wird nicht "
                    "automatisch unterstützt (nur DHL).")
        fehler = _check_sale(sale)
        if fehler:
            return fehler
        db.commit()
        return f"Sendungsstatus: {sale.tracking_status_text or sale.tracking_status}."
    finally:
        db.close()


async def _tracking_loop():
    """Fragt den Sendungsstatus regelmäßig ab (Standard: alle 12 h = 2x täglich)."""
    await asyncio.sleep(120)      # Start nicht ausbremsen
    while True:
        try:
            await asyncio.to_thread(update_tracking)
        except Exception:
            log.exception("Sendungsverfolgung fehlgeschlagen")
        await asyncio.sleep(config.TRACKING_INTERVAL_HOURS * 3600)


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
            await asyncio.to_thread(auto_complete_sales)   # zugestellt -> abgeschlossen
            await asyncio.to_thread(archive_old_sales)
        except Exception:  # Loop niemals sterben lassen
            log.exception("Auto-Archivierung/Abschluss fehlgeschlagen")
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
    if config.TRACKING_INTERVAL_HOURS > 0 and carriers.is_configured():
        tasks.append(asyncio.create_task(_tracking_loop()))
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
