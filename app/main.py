"""Warenwirtschaftssystem — FastAPI-App."""
import csv
import io
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Depends, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config, ebay
from .database import Base, engine, get_db
from .models import Article, ArticleImage, STATUSES, CONDITIONS

# Tabellen anlegen
Base.metadata.create_all(engine)

app = FastAPI(title="Warenwirtschaftssystem")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Statische Dateien & hochgeladene Bilder ausliefern
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/uploads", StaticFiles(directory=str(config.UPLOAD_DIR)), name="uploads")

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


# ---------------------------------------------------------------------------
# Jinja-Filter (deutsche Formatierung)
# ---------------------------------------------------------------------------
def format_eur(value) -> str:
    if value is None:
        return "–"
    s = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


def format_date(value) -> str:
    if not value:
        return "–"
    return value.strftime("%d.%m.%Y")


templates.env.filters["eur"] = format_eur
templates.env.filters["date"] = format_date


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def parse_float(value: str | None) -> float:
    if value is None or str(value).strip() == "":
        return 0.0
    try:
        return float(str(value).replace(",", ".").strip())
    except ValueError:
        return 0.0


def apply_form(article: Article, data: dict) -> None:
    """Übernimmt Formulardaten in ein Article-Objekt."""
    article.title = (data.get("title") or "").strip() or "Ohne Titel"
    article.description = (data.get("description") or "").strip()
    article.category = (data.get("category") or "").strip()
    article.condition = (data.get("condition") or "").strip()
    new_status = (data.get("status") or "Entwurf").strip()

    article.purchase_cost = parse_float(data.get("purchase_cost"))
    article.listing_price = parse_float(data.get("listing_price"))
    article.sold_price = parse_float(data.get("sold_price"))
    article.shipping_method = (data.get("shipping_method") or "").strip()
    article.shipping_cost = parse_float(data.get("shipping_cost"))
    article.fees = parse_float(data.get("fees"))

    article.ebay_url = (data.get("ebay_url") or "").strip()
    article.ebay_item_id = (data.get("ebay_item_id") or "").strip()
    article.kleinanzeigen_url = (data.get("kleinanzeigen_url") or "").strip()
    article.offered_ebay = data.get("offered_ebay") is not None
    article.offered_kleinanzeigen = data.get("offered_kleinanzeigen") is not None

    # Verkaufsdatum automatisch setzen/entfernen
    if new_status == "Verkauft" and article.status != "Verkauft":
        article.sold_at = datetime.now(timezone.utc)
    elif new_status != "Verkauft":
        article.sold_at = None
    article.status = new_status


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    total = db.scalar(select(func.count(Article.id))) or 0

    status_counts = {s: 0 for s in STATUSES}
    for status, count in db.execute(
        select(Article.status, func.count(Article.id)).group_by(Article.status)
    ):
        status_counts[status] = count

    sold = db.scalars(select(Article).where(Article.status == "Verkauft")).all()
    umsatz = sum(a.sold_price for a in sold)
    kosten = sum(a.purchase_cost + a.shipping_cost + a.fees for a in sold)
    gewinn = round(umsatz - kosten, 2)

    # In noch nicht verkauften Artikeln gebundenes Kapital
    offen = db.scalars(
        select(Article).where(Article.status.in_(["Entwurf", "Angeboten", "Reserviert"]))
    ).all()
    gebundenes_kapital = sum(a.purchase_cost for a in offen)
    potenzieller_umsatz = sum(a.listing_price for a in offen)

    ctx = {
        "request": request,
        "total": total,
        "status_counts": status_counts,
        "umsatz": umsatz,
        "kosten": kosten,
        "gewinn": gewinn,
        "verkauft_anzahl": len(sold),
        "offen_anzahl": len(offen),
        "gebundenes_kapital": gebundenes_kapital,
        "potenzieller_umsatz": potenzieller_umsatz,
        "ebay_configured": ebay.is_configured(),
    }
    return templates.TemplateResponse("dashboard.html", ctx)


# ---------------------------------------------------------------------------
# Artikelliste
# ---------------------------------------------------------------------------
@app.get("/articles", response_class=HTMLResponse)
def list_articles(
    request: Request,
    q: str = "",
    status: str = "",
    db: Session = Depends(get_db),
):
    stmt = select(Article).order_by(Article.updated_at.desc())
    if status:
        stmt = stmt.where(Article.status == status)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Article.title.ilike(like) | Article.category.ilike(like))
    articles = db.scalars(stmt).all()

    ctx = {
        "request": request,
        "articles": articles,
        "statuses": STATUSES,
        "q": q,
        "active_status": status,
    }
    return templates.TemplateResponse("articles.html", ctx)


# ---------------------------------------------------------------------------
# Artikel anlegen
# ---------------------------------------------------------------------------
@app.get("/articles/new", response_class=HTMLResponse)
def new_article(request: Request):
    ctx = {
        "request": request,
        "article": None,
        "statuses": STATUSES,
        "conditions": CONDITIONS,
        "fee_percent": config.DEFAULT_EBAY_FEE_PERCENT,
    }
    return templates.TemplateResponse("article_form.html", ctx)


@app.post("/articles/new")
async def create_article(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    article = Article()
    apply_form(article, form)
    db.add(article)
    db.commit()
    return RedirectResponse(f"/articles/{article.id}", status_code=303)


# ---------------------------------------------------------------------------
# Artikel-Detail
# ---------------------------------------------------------------------------
def _get_article(db: Session, article_id: int) -> Article:
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    return article


@app.get("/articles/{article_id}", response_class=HTMLResponse)
def article_detail(article_id: int, request: Request, db: Session = Depends(get_db)):
    article = _get_article(db, article_id)
    return templates.TemplateResponse(
        "article_detail.html", {"request": request, "article": article}
    )


# ---------------------------------------------------------------------------
# Artikel bearbeiten
# ---------------------------------------------------------------------------
@app.get("/articles/{article_id}/edit", response_class=HTMLResponse)
def edit_article(article_id: int, request: Request, db: Session = Depends(get_db)):
    article = _get_article(db, article_id)
    ctx = {
        "request": request,
        "article": article,
        "statuses": STATUSES,
        "conditions": CONDITIONS,
        "fee_percent": config.DEFAULT_EBAY_FEE_PERCENT,
    }
    return templates.TemplateResponse("article_form.html", ctx)


@app.post("/articles/{article_id}/edit")
async def update_article(article_id: int, request: Request, db: Session = Depends(get_db)):
    article = _get_article(db, article_id)
    form = await request.form()
    apply_form(article, form)
    db.commit()
    return RedirectResponse(f"/articles/{article.id}", status_code=303)


@app.post("/articles/{article_id}/delete")
def delete_article(article_id: int, db: Session = Depends(get_db)):
    article = _get_article(db, article_id)
    # zugehörige Bilddateien entfernen
    for img in article.images:
        path = config.UPLOAD_DIR / img.filename
        if path.exists():
            path.unlink()
    db.delete(article)
    db.commit()
    return RedirectResponse("/articles", status_code=303)


# ---------------------------------------------------------------------------
# Bilder
# ---------------------------------------------------------------------------
@app.post("/articles/{article_id}/images")
async def upload_image(
    article_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    article = _get_article(db, article_id)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXT:
        raise HTTPException(status_code=400, detail="Ungültiges Bildformat")

    filename = f"{uuid.uuid4().hex}{ext}"
    dest = config.UPLOAD_DIR / filename
    with dest.open("wb") as f:
        f.write(await file.read())

    db.add(ArticleImage(article_id=article.id, filename=filename))
    db.commit()
    return RedirectResponse(f"/articles/{article_id}", status_code=303)


@app.post("/articles/{article_id}/images/{image_id}/delete")
def delete_image(article_id: int, image_id: int, db: Session = Depends(get_db)):
    img = db.get(ArticleImage, image_id)
    if img and img.article_id == article_id:
        path = config.UPLOAD_DIR / img.filename
        if path.exists():
            path.unlink()
        db.delete(img)
        db.commit()
    return RedirectResponse(f"/articles/{article_id}", status_code=303)


# ---------------------------------------------------------------------------
# CSV-Export
# ---------------------------------------------------------------------------
@app.get("/export.csv")
def export_csv(db: Session = Depends(get_db)):
    articles = db.scalars(select(Article).order_by(Article.id)).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([
        "ID", "Titel", "Kategorie", "Zustand", "Status",
        "Einkaufskosten", "Angebotspreis", "Verkaufspreis",
        "Versandart", "Versandkosten", "Gebuehren", "Gewinn",
        "eBay-Link", "Kleinanzeigen-Link",
        "Angelegt", "Verkauft",
    ])
    for a in articles:
        writer.writerow([
            a.id, a.title, a.category, a.condition, a.status,
            f"{a.purchase_cost:.2f}", f"{a.listing_price:.2f}", f"{a.sold_price:.2f}",
            a.shipping_method, f"{a.shipping_cost:.2f}", f"{a.fees:.2f}",
            f"{a.profit:.2f}" if a.profit is not None else "",
            a.ebay_url, a.kleinanzeigen_url,
            a.created_at.strftime("%d.%m.%Y") if a.created_at else "",
            a.sold_at.strftime("%d.%m.%Y") if a.sold_at else "",
        ])

    buffer.seek(0)
    # BOM voranstellen, damit Excel Umlaute korrekt anzeigt
    content = "﻿" + buffer.getvalue()
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=warensystem_export.csv"},
    )


# ---------------------------------------------------------------------------
# eBay-Sync (Platzhalter)
# ---------------------------------------------------------------------------
@app.post("/ebay/sync")
def ebay_sync(db: Session = Depends(get_db)):
    # Bewusst noch nicht aktiv — siehe app/ebay.py
    return RedirectResponse("/", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok"}
