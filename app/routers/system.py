"""Routen: system."""
import csv
import io
import urllib.parse
from datetime import datetime

from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from .. import backup, carriers, ebay
from ..database import engine, get_db
from ..maintenance import refresh_sale_tracking, update_tracking
from ..migrations import run_migrations
from ..models import Article, Sale

router = APIRouter()


@router.get("/export.csv")
def export_csv(year: int | None = None, db: Session = Depends(get_db)):
    """Bestandsliste: alle Artikel mit Bestand und Verkaufssummen."""
    articles = db.scalars(
        select(Article).options(selectinload(Article.sales)).order_by(Article.id)
    ).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([
        "Artikelnr", "ID", "Titel", "Kategorie", "Zustand", "Status", "Lagerplatz", "Tags",
        "Bestand", "Einkauf je Stueck", "Angebotspreis", "Bestandswert",
        "Verkauft Stueck", "Umsatz", "Gewinn",
        "eBay-Link", "Kleinanzeigen-Link", "Notiz", "Angelegt",
    ])
    for a in articles:
        writer.writerow([
            a.article_no, a.id, a.title, a.category, a.condition, a.status,
            a.storage_location, a.tags,
            a.quantity, f"{a.purchase_cost:.2f}", f"{a.listing_price:.2f}",
            f"{a.stock_value:.2f}",
            a.sold_quantity, f"{a.revenue:.2f}",
            f"{a.total_profit:.2f}" if a.total_profit is not None else "",
            a.ebay_url, a.kleinanzeigen_url, a.note,
            a.created_at.strftime("%d.%m.%Y") if a.created_at else "",
        ])

    buffer.seek(0)
    content = "﻿" + buffer.getvalue()   # BOM für Excel
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=warensystem_bestand.csv"},
    )


@router.get("/export-sales.csv")
def export_sales_csv(year: int | None = None, db: Session = Depends(get_db)):
    """Verkaufsliste: jeder Verkauf eine Zeile (für Buchhaltung/Steuer)."""
    sales = db.scalars(
        select(Sale).options(joinedload(Sale.article)).order_by(Sale.sold_at)
    ).all()
    if year is not None:
        sales = [s for s in sales if s.sold_at and s.sold_at.year == year]

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([
        "Verkauf-Nr", "Verkauft am", "Artikelnr", "Titel", "Stueck",
        "Verkaufspreis", "Einkauf je Stueck", "Gebuehren",
        "Versandart", "Versandkosten", "Versand zahlt",
        "Gewinn", "Marge %",
        "Verkauft ueber", "Kaeufer", "Zahlungsart",
        "Versanddienstleister", "Trackingnummer", "Notiz",
        "Bestellt", "Versendet",
    ])
    for s in sales:
        a = s.article
        writer.writerow([
            f"LS-{s.id:05d}",
            s.sold_at.strftime("%d.%m.%Y") if s.sold_at else "",
            a.article_no if a else "", a.title if a else "",
            s.quantity,
            f"{s.sold_price:.2f}", f"{s.unit_purchase_cost:.2f}", f"{s.fees:.2f}",
            s.shipping_method, f"{s.shipping_cost:.2f}", s.shipping_payer,
            f"{s.profit:.2f}", f"{s.margin:.1f}" if s.margin is not None else "",
            s.sale_platform, s.buyer_name, s.payment_method,
            s.carrier_label, s.tracking_number, s.note,
            s.order_date.strftime("%d.%m.%Y") if s.order_date else "",
            s.shipped_at.strftime("%d.%m.%Y") if s.shipped_at else "",
        ])

    buffer.seek(0)
    content = "﻿" + buffer.getvalue()
    suffix = f"_{year}" if year is not None else ""
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=warensystem_verkaeufe{suffix}.csv"},
    )


# ---------------------------------------------------------------------------
# eBay-Sync (Platzhalter)
# ---------------------------------------------------------------------------
@router.post("/ebay/sync")
def ebay_sync(db: Session = Depends(get_db)):
    # Bewusst noch nicht aktiv — siehe app/ebay.py
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# Sendungsverfolgung manuell anstoßen
# ---------------------------------------------------------------------------
def _back_with(back: str, key: str, text: str) -> str:
    sep = "&" if "?" in back else "?"
    return f"{back}{sep}{key}={urllib.parse.quote(text)}"


@router.post("/tracking/refresh")
async def tracking_refresh(request: Request):
    """Prüft sofort alle offenen DHL-Sendungen."""
    form = await request.form()
    back = form.get("back") or "/"
    run = update_tracking()

    if run.errors:
        return RedirectResponse(_back_with(back, "error", " ".join(run.errors[:2])),
                                status_code=303)
    if run.checked == 0:
        note = "Keine offenen DHL-Sendungen zum Prüfen."
    else:
        note = f"Geprüft: {run.updated} von {run.checked} Sendungen aktualisiert."
    return RedirectResponse(_back_with(back, "msg", note), status_code=303)


@router.post("/sales/{sale_id}/tracking-refresh")
async def sale_tracking_refresh(sale_id: int, request: Request):
    """Prüft sofort den Status einer einzelnen Sendung."""
    form = await request.form()
    back = form.get("back") or f"/sales/{sale_id}/edit"
    note = refresh_sale_tracking(sale_id) or "Sendungsstatus geprüft."
    return RedirectResponse(_back_with(back, "msg", note), status_code=303)


# ---------------------------------------------------------------------------
# Backup & Restore
# ---------------------------------------------------------------------------
@router.get("/backup.zip")
def backup_download():
    data = backup.create_backup()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=warensystem-backup-{stamp}.zip"},
    )


@router.post("/restore")
async def restore_upload(file: UploadFile = File(...)):
    data = await file.read()
    try:
        backup.restore_backup(data)
        run_migrations(engine)  # ältere Backups ggf. auf aktuelles Schema heben
    except ValueError as e:
        return RedirectResponse(f"/?error={urllib.parse.quote(str(e))}", status_code=303)
    return RedirectResponse("/?restored=1", status_code=303)
