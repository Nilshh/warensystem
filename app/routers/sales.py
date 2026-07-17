"""Routen: sales."""
import urllib.parse
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .. import config
from ..database import get_db
from ..models import Sale, SHIPPING_METHODS, SHIPPING_OPTIONS, SHIPPING_PAYERS, SALE_PLATFORMS
from ..services import parse_float, parse_date, _sold_years, _sync_stock_status, _get_sale
from ..web import templates, format_eur

router = APIRouter()


@router.get("/sales", response_class=HTMLResponse)
def sales_list(
    request: Request, year: int | None = None, q: str = "",
    msg: str = "", error: str = "", db: Session = Depends(get_db),
):
    """Übersicht aller Verkäufe (mit Jahresfilter und Suche)."""
    sales = db.scalars(
        select(Sale).options(joinedload(Sale.article)).order_by(Sale.sold_at.desc())
    ).all()
    years = _sold_years(db)
    if year:
        sales = [s for s in sales if s.sold_at and s.sold_at.year == year]
    if q:
        needle = q.lower()
        sales = [
            s for s in sales
            if needle in (s.buyer_name or "").lower()
            or needle in (s.article.title if s.article else "").lower()
            or needle in (s.article.article_no if s.article else "").lower()
        ]
    return templates.TemplateResponse(
        "sales_list.html",
        {
            "request": request, "sales": sales, "years": years,
            "year": year, "q": q, "msg": msg, "error": error,
            "umsatz": round(sum(s.sold_price for s in sales), 2),
            "gewinn": round(sum(s.profit for s in sales), 2),
            "stueck": sum(s.quantity for s in sales),
        },
    )


@router.get("/sales/{sale_id}/edit", response_class=HTMLResponse)
def sale_edit_form(sale_id: int, request: Request, db: Session = Depends(get_db)):
    sale = _get_sale(db, sale_id)
    return templates.TemplateResponse(
        "sale_form.html",
        {
            "request": request, "sale": sale, "article": sale.article,
            "shipping_methods": SHIPPING_METHODS,
            "shipping_options": SHIPPING_OPTIONS,
            "shipping_payers": SHIPPING_PAYERS,
            "sale_platforms": SALE_PLATFORMS,
            "fee_percent": config.DEFAULT_EBAY_FEE_PERCENT,
            # Höchstmenge: bisherige Menge + noch verfügbarer Bestand
            "max_qty": sale.quantity + (sale.article.quantity if sale.article else 0),
        },
    )


@router.post("/sales/{sale_id}/edit")
async def sale_edit(sale_id: int, request: Request, db: Session = Depends(get_db)):
    sale = _get_sale(db, sale_id)
    article = sale.article
    form = await request.form()

    new_qty = max(1, int(parse_float(form.get("quantity")) or 1))
    delta = new_qty - sale.quantity          # positiv = mehr verkauft als bisher
    if article and delta > article.quantity:
        msg = urllib.parse.quote(
            f"Nicht genug Bestand: nur {sale.quantity + article.quantity} Stück möglich."
        )
        return RedirectResponse(f"/sales/{sale_id}/edit?error={msg}", status_code=303)

    sale.quantity = new_qty
    sale.sold_price = parse_float(form.get("sold_price"))
    sale.unit_purchase_cost = parse_float(form.get("unit_purchase_cost"))
    sale.fees = parse_float(form.get("fees"))
    sale.shipping_method = (form.get("shipping_method") or "").strip()
    sale.shipping_cost = parse_float(form.get("shipping_cost"))
    sale.shipping_payer = (form.get("shipping_payer") or "Käufer").strip()
    sale.sale_platform = (form.get("sale_platform") or "").strip()
    sale.buyer_name = (form.get("buyer_name") or "").strip()
    sale.buyer_address = (form.get("buyer_address") or "").strip()
    sale.payment_method = (form.get("payment_method") or "").strip()
    sale.tracking_carrier = (form.get("tracking_carrier") or "").strip()
    sale.tracking_number = (form.get("tracking_number") or "").strip()
    sale.note = (form.get("note") or "").strip()
    sale.order_date = parse_date(form.get("order_date"))
    sale.shipped_at = parse_date(form.get("shipped_at"))
    sold_at = parse_date(form.get("sold_at"))
    if sold_at:
        sale.sold_at = sold_at

    if article:
        article.quantity -= delta          # Bestand entsprechend korrigieren
        _sync_stock_status(article)
    db.commit()

    note = urllib.parse.quote(f"Verkauf korrigiert. Gewinn: {format_eur(sale.profit)}.")
    return RedirectResponse(f"/sales?msg={note}", status_code=303)


@router.post("/sales/{sale_id}/delete")
def sale_delete(sale_id: int, db: Session = Depends(get_db)):
    """Löscht einen Verkauf und bucht den Bestand zurück."""
    sale = _get_sale(db, sale_id)
    article = sale.article
    qty = sale.quantity
    db.delete(sale)
    db.flush()
    if article:
        article.quantity += qty            # Ware ist wieder da
        _sync_stock_status(article)
    db.commit()
    note = urllib.parse.quote(f"Verkauf gelöscht, {qty} Stück zurück im Bestand.")
    return RedirectResponse(f"/sales?msg={note}", status_code=303)


@router.get("/sales/{sale_id}/lieferschein", response_class=HTMLResponse)
def lieferschein(sale_id: int, request: Request, db: Session = Depends(get_db)):
    """Druckbarer Lieferschein/Packzettel für einen einzelnen Verkauf."""
    sale = db.get(Sale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Verkauf nicht gefunden")
    seller = {
        "name": config.SELLER_NAME,
        "address": config.SELLER_ADDRESS.replace("\\n", "\n"),
        "email": config.SELLER_EMAIL,
        "phone": config.SELLER_PHONE,
    }
    date = sale.shipped_at or sale.sold_at or datetime.now(timezone.utc)
    return templates.TemplateResponse(
        "lieferschein.html",
        {"request": request, "sale": sale, "article": sale.article,
         "seller": seller, "date": date},
    )


# ---------------------------------------------------------------------------
# Artikel bearbeiten
