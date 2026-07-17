"""Routen: reports."""
from datetime import timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import Article, Sale
from ..services import _age_days
from ..web import templates

router = APIRouter()


@router.get("/reports", response_class=HTMLResponse)
def reports(request: Request, days: int = 90, db: Session = Depends(get_db)):
    """Auswertungen: Ladenhüter, Plattform-Vergleich, Verkaufsdauer je Kategorie."""
    # --- Ladenhüter: auf Bestand und lange nicht bewegt ---------------------
    ladenhueter = []
    for a in db.scalars(
        select(Article).where(Article.quantity > 0, Article.status != "Archiviert")
    ).all():
        alter = _age_days(a.created_at)
        if alter is not None and alter >= days:
            ladenhueter.append({"article": a, "alter": alter})
    ladenhueter.sort(key=lambda x: x["alter"], reverse=True)
    gebundenes_kapital = round(sum(x["article"].stock_value for x in ladenhueter), 2)

    # Verkäufe einmal laden (mit Artikel) und für beide Auswertungen nutzen
    alle_verkaeufe = db.scalars(select(Sale).options(joinedload(Sale.article))).all()

    # --- Plattform-Vergleich ------------------------------------------------
    plattformen: dict[str, dict] = {}
    for s in alle_verkaeufe:
        key = s.sale_platform or "ohne Angabe"
        p = plattformen.setdefault(key, {"name": key, "stueck": 0, "umsatz": 0.0,
                                         "gewinn": 0.0, "verkaeufe": 0})
        p["stueck"] += s.quantity
        p["umsatz"] += s.sold_price
        p["gewinn"] += s.profit
        p["verkaeufe"] += 1
    for p in plattformen.values():
        p["umsatz"] = round(p["umsatz"], 2)
        p["gewinn"] = round(p["gewinn"], 2)
        p["marge"] = round(p["gewinn"] / p["umsatz"] * 100, 1) if p["umsatz"] else None
    plattform_liste = sorted(plattformen.values(), key=lambda p: p["umsatz"], reverse=True)

    # --- Verkaufsdauer & Marge je Kategorie ---------------------------------
    kategorien: dict[str, dict] = {}
    for s in alle_verkaeufe:
        a = s.article
        if not a:
            continue
        key = a.category or "ohne Kategorie"
        k = kategorien.setdefault(key, {"name": key, "stueck": 0, "umsatz": 0.0,
                                        "gewinn": 0.0, "tage": []})
        k["stueck"] += s.quantity
        k["umsatz"] += s.sold_price
        k["gewinn"] += s.profit
        if a.created_at and s.sold_at:
            created = a.created_at if a.created_at.tzinfo else a.created_at.replace(tzinfo=timezone.utc)
            sold = s.sold_at if s.sold_at.tzinfo else s.sold_at.replace(tzinfo=timezone.utc)
            dauer = (sold - created).days
            if dauer >= 0:
                k["tage"].append(dauer)
    for k in kategorien.values():
        k["umsatz"] = round(k["umsatz"], 2)
        k["gewinn"] = round(k["gewinn"], 2)
        k["marge"] = round(k["gewinn"] / k["umsatz"] * 100, 1) if k["umsatz"] else None
        k["dauer"] = round(sum(k["tage"]) / len(k["tage"])) if k["tage"] else None
    kategorie_liste = sorted(kategorien.values(), key=lambda k: k["gewinn"], reverse=True)

    return templates.TemplateResponse(
        "reports.html",
        {
            "request": request, "days": days,
            "ladenhueter": ladenhueter,
            "gebundenes_kapital": gebundenes_kapital,
            "plattformen": plattform_liste,
            "kategorien": kategorie_liste,
        },
    )
