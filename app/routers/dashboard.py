"""Routen: dashboard."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from .. import carriers, config, ebay
from ..database import get_db
from ..models import Article, Sale, STATUSES, FULFILLMENT_CANCELLED
from ..services import MONTH_NAMES, _sold_years
from ..web import templates

router = APIRouter()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
MONTH_NAMES = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
               "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]

@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    year: int | None = None,
    restored: int = 0,
    error: str = "",
    db: Session = Depends(get_db),
):
    total = db.scalar(select(func.count(Article.id))) or 0

    status_counts = {s: 0 for s in STATUSES}
    for status, count in db.execute(
        select(Article.status, func.count(Article.id)).group_by(Article.status)
    ):
        status_counts[status] = count

    years = _sold_years(db)
    if year is None:
        year = years[0] if years else datetime.now(timezone.utc).year

    # Alle zählenden Verkäufe des gewählten Jahres (Stornos ausgenommen)
    all_sales = db.scalars(
        select(Sale).where(Sale.sold_at.is_not(None),
                           Sale.fulfillment != FULFILLMENT_CANCELLED)
    ).all()
    sold = [s for s in all_sales if s.sold_at.year == year]

    umsatz = round(sum(s.sold_price for s in sold), 2)
    gewinn = round(sum(s.profit for s in sold), 2)
    kosten = round(umsatz - gewinn, 2)

    # Monatliche Aggregation für das Jahr
    monthly = []
    for m in range(1, 13):
        items = [s for s in sold if s.sold_at.month == m]
        monthly.append({
            "name": MONTH_NAMES[m - 1],
            "umsatz": round(sum(s.sold_price for s in items), 2),
            "gewinn": round(sum(s.profit for s in items), 2),
            "count": sum(s.quantity for s in items),
        })
    chart_max = max([m["umsatz"] for m in monthly] + [m["gewinn"] for m in monthly] + [1])

    # Sendungen, die auffällig lange unterwegs sind
    haengend = []
    if carriers.is_configured():
        grenze = datetime.now(timezone.utc) - timedelta(days=config.TRACKING_STUCK_DAYS)
        for s in db.scalars(
            select(Sale)
            .options(joinedload(Sale.article))
            .where(Sale.tracking_number != "",
                   Sale.tracking_status == carriers.UNTERWEGS)
        ).all():
            seit = s.shipped_at or s.sold_at
            if seit and seit.tzinfo is None:
                seit = seit.replace(tzinfo=timezone.utc)
            if seit and seit <= grenze:
                haengend.append({"sale": s, "tage": (datetime.now(timezone.utc) - seit).days})
        haengend.sort(key=lambda x: x["tage"], reverse=True)

    # Offene Aufgaben: Verkäufe, die noch abgewickelt werden müssen
    zu_versenden = db.scalars(
        select(Sale)
        .options(joinedload(Sale.article))
        .where(Sale.fulfillment.in_(("Verkauft", "Bezahlt")))
        .order_by(Sale.sold_at)
    ).all()

    # Bestand: gebundenes Kapital und potenzieller Umsatz (jahresunabhängig)
    offen = db.scalars(select(Article).where(Article.quantity > 0)).all()
    gebundenes_kapital = round(sum(a.stock_value for a in offen), 2)
    potenzieller_umsatz = round(sum(a.listing_price * a.quantity for a in offen), 2)
    bestand_stueck = sum(a.quantity for a in offen)

    ctx = {
        "request": request,
        "total": total,
        "status_counts": status_counts,
        "umsatz": umsatz,
        "kosten": kosten,
        "gewinn": gewinn,
        "verkauft_anzahl": sum(s.quantity for s in sold),
        "offen_anzahl": len(offen),
        "bestand_stueck": bestand_stueck,
        "gebundenes_kapital": gebundenes_kapital,
        "potenzieller_umsatz": potenzieller_umsatz,
        "ebay_configured": ebay.is_configured(),
        "year": year,
        "years": years,
        "monthly": monthly,
        "chart_max": chart_max,
        "restored": restored,
        "error": error,
        "haengend": haengend,
        "stuck_days": config.TRACKING_STUCK_DAYS,
        "tracking_configured": carriers.is_configured(),
        "zu_versenden": zu_versenden,
    }
    return templates.TemplateResponse("dashboard.html", ctx)

@router.get("/health")
def health():
    return {"status": "ok"}
