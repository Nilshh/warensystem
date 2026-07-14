"""Warenwirtschaftssystem — FastAPI-App."""
import csv
import io
import urllib.parse
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
from .migrations import run_migrations
from .models import (
    Article, ArticleImage,
    STATUSES, CONDITIONS, SHIPPING_METHODS, SALE_PLATFORMS,
)

# Tabellen anlegen und fehlende Spalten nachziehen (leichtgewichtige Migration)
Base.metadata.create_all(engine)
run_migrations(engine)

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


def parse_date(value: str | None) -> datetime | None:
    """Erwartet ein HTML-Date-Feld (YYYY-MM-DD)."""
    if not value or not str(value).strip():
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d")
    except ValueError:
        return None


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

    # Schlagworte normalisieren (kommagetrennt, ohne Leereinträge)
    raw_tags = (data.get("tags") or "").split(",")
    article.tags = ", ".join(t.strip() for t in raw_tags if t.strip())

    # Käufer- & Versandabwicklung
    article.sale_platform = (data.get("sale_platform") or "").strip()
    article.buyer_name = (data.get("buyer_name") or "").strip()
    article.payment_method = (data.get("payment_method") or "").strip()
    article.tracking_carrier = (data.get("tracking_carrier") or "").strip()
    article.tracking_number = (data.get("tracking_number") or "").strip()
    article.order_date = parse_date(data.get("order_date"))
    article.shipped_at = parse_date(data.get("shipped_at"))

    # Verkaufsdatum automatisch setzen/entfernen
    if new_status == "Verkauft" and article.status != "Verkauft":
        article.sold_at = datetime.now(timezone.utc)
    elif new_status != "Verkauft":
        article.sold_at = None
    article.status = new_status


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
MONTH_NAMES = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
               "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def _sold_years(db: Session) -> list[int]:
    """Alle Jahre, in denen etwas verkauft wurde (absteigend)."""
    rows = db.scalars(
        select(Article.sold_at).where(Article.status == "Verkauft", Article.sold_at.is_not(None))
    ).all()
    return sorted({d.year for d in rows}, reverse=True)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, year: int | None = None, db: Session = Depends(get_db)):
    total = db.scalar(select(func.count(Article.id))) or 0

    status_counts = {s: 0 for s in STATUSES}
    for status, count in db.execute(
        select(Article.status, func.count(Article.id)).group_by(Article.status)
    ):
        status_counts[status] = count

    years = _sold_years(db)
    if year is None:
        year = years[0] if years else datetime.now(timezone.utc).year

    sold_all = db.scalars(select(Article).where(Article.status == "Verkauft")).all()
    # Nach gewähltem Jahr filtern (für Kennzahlen + Diagramm)
    sold = [a for a in sold_all if a.sold_at and a.sold_at.year == year]

    umsatz = sum(a.sold_price for a in sold)
    kosten = sum(a.purchase_cost + a.shipping_cost + a.fees for a in sold)
    gewinn = round(umsatz - kosten, 2)

    # Monatliche Aggregation für das Jahr
    monthly = []
    for m in range(1, 13):
        items = [a for a in sold if a.sold_at and a.sold_at.month == m]
        m_umsatz = sum(a.sold_price for a in items)
        m_gewinn = round(sum((a.profit or 0) for a in items), 2)
        monthly.append({
            "name": MONTH_NAMES[m - 1],
            "umsatz": m_umsatz,
            "gewinn": m_gewinn,
            "count": len(items),
        })
    chart_max = max([m["umsatz"] for m in monthly] + [m["gewinn"] for m in monthly] + [1])

    # In noch nicht verkauften Artikeln gebundenes Kapital (jahresunabhängig)
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
        "year": year,
        "years": years,
        "monthly": monthly,
        "chart_max": chart_max,
    }
    return templates.TemplateResponse("dashboard.html", ctx)


# ---------------------------------------------------------------------------
# Artikelliste
# ---------------------------------------------------------------------------
SORT_COLUMNS = {
    "title": Article.title,
    "status": Article.status,
    "listing_price": Article.listing_price,
    "sold_price": Article.sold_price,
    "updated_at": Article.updated_at,
}


@app.get("/articles", response_class=HTMLResponse)
def list_articles(
    request: Request,
    q: str = "",
    status: str = "",
    tag: str = "",
    sort: str = "updated_at",
    dir: str = "desc",
    db: Session = Depends(get_db),
):
    column = SORT_COLUMNS.get(sort, Article.updated_at)
    order = column.asc() if dir == "asc" else column.desc()

    stmt = select(Article).order_by(order)
    if status:
        stmt = stmt.where(Article.status == status)
    if tag:
        stmt = stmt.where(Article.tags.ilike(f"%{tag}%"))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            Article.title.ilike(like)
            | Article.category.ilike(like)
            | Article.tags.ilike(like)
        )
    articles = db.scalars(stmt).all()

    ctx = {
        "request": request,
        "articles": articles,
        "statuses": STATUSES,
        "q": q,
        "active_status": status,
        "active_tag": tag,
        "sort": sort,
        "dir": dir,
    }
    return templates.TemplateResponse("articles.html", ctx)


# ---------------------------------------------------------------------------
# Artikel anlegen
# ---------------------------------------------------------------------------
def _form_context(request: Request, article: Article | None, error: str = "") -> dict:
    return {
        "request": request,
        "article": article,
        "statuses": STATUSES,
        "conditions": CONDITIONS,
        "shipping_methods": SHIPPING_METHODS,
        "sale_platforms": SALE_PLATFORMS,
        "fee_percent": config.DEFAULT_EBAY_FEE_PERCENT,
        "ebay_import_enabled": ebay.import_supported(),
        "error": error,
    }


@app.get("/articles/new", response_class=HTMLResponse)
def new_article(request: Request, error: str = ""):
    return templates.TemplateResponse("article_form.html", _form_context(request, None, error))


@app.post("/articles/new")
async def create_article(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    article = Article()
    apply_form(article, form)
    db.add(article)
    db.commit()
    return RedirectResponse(f"/articles/{article.id}", status_code=303)


def _download_item_images(db: Session, article: Article, image_urls: list[str]) -> None:
    for pos, img_url in enumerate(image_urls):
        ext = Path(urllib.parse.urlparse(img_url).path).suffix.lower()
        if ext not in ALLOWED_IMAGE_EXT:
            ext = ".jpg"
        filename = f"{uuid.uuid4().hex}{ext}"
        if ebay.download_image(img_url, config.UPLOAD_DIR / filename):
            db.add(ArticleImage(article_id=article.id, filename=filename, position=pos))


def _create_article_from_item(db: Session, item: dict, fallback_url: str = "") -> Article:
    """Legt aus einem geladenen eBay-Item einen Entwurf an (inkl. Bilder)."""
    article = Article(
        title=item["title"] or "eBay-Import",
        description=item["description"],
        condition=item["condition"],
        status="Entwurf",
        listing_price=item["price"],
        ebay_url=item["item_web_url"] or fallback_url.strip(),
        ebay_item_id=item["ebay_item_id"],
        offered_ebay=True,
    )
    db.add(article)
    db.flush()  # article.id verfügbar machen
    _download_item_images(db, article, item["image_urls"])
    return article


@app.post("/articles/import-ebay")
async def import_from_ebay(
    ebay_url: str = Form(""), db: Session = Depends(get_db)
):
    """Legt aus einem eBay-Link einen Entwurf an (Browse API) und lädt Bilder."""
    try:
        item = ebay.fetch_item(ebay_url)
    except ebay.EbayError as e:
        msg = urllib.parse.quote(str(e))
        return RedirectResponse(f"/articles/new?error={msg}", status_code=303)

    article = _create_article_from_item(db, item, ebay_url)
    db.commit()
    return RedirectResponse(f"/articles/{article.id}/edit", status_code=303)


# Obergrenze pro Massenimport (schützt vor sehr langen Requests)
BULK_IMPORT_LIMIT = 100


@app.post("/articles/import-ebay-bulk", response_class=HTMLResponse)
async def import_from_ebay_bulk(
    request: Request, ebay_urls: str = Form(""), db: Session = Depends(get_db)
):
    """Importiert mehrere eBay-Links (einer pro Zeile) in einem Rutsch."""
    lines = [ln.strip() for ln in ebay_urls.splitlines() if ln.strip()]
    truncated = len(lines) > BULK_IMPORT_LIMIT
    lines = lines[:BULK_IMPORT_LIMIT]

    # bereits vorhandene eBay-Artikelnummern (Dedupe)
    existing = {
        i for (i,) in db.execute(
            select(Article.ebay_item_id).where(Article.ebay_item_id != "")
        )
    }

    results = []
    imported = skipped = failed = 0
    seen_in_batch: set[str] = set()

    for line in lines:
        item_id = ebay.extract_item_id(line)
        if item_id and (item_id in existing or item_id in seen_in_batch):
            skipped += 1
            results.append({"input": line, "status": "skipped",
                            "message": f"Bereits vorhanden (Artikelnr. {item_id})"})
            continue
        try:
            item = ebay.fetch_item(line)
        except ebay.EbayError as e:
            failed += 1
            results.append({"input": line, "status": "failed", "message": str(e)})
            continue

        article = _create_article_from_item(db, item, line)
        db.flush()
        seen_in_batch.add(article.ebay_item_id)
        imported += 1
        results.append({
            "input": line, "status": "ok", "message": item["title"] or "Import",
            "article_id": article.id,
        })

    db.commit()

    ctx = {
        "request": request,
        "results": results,
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "truncated": truncated,
        "limit": BULK_IMPORT_LIMIT,
    }
    return templates.TemplateResponse("import_result.html", ctx)


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
    return templates.TemplateResponse("article_form.html", _form_context(request, article))


@app.post("/articles/{article_id}/edit")
async def update_article(article_id: int, request: Request, db: Session = Depends(get_db)):
    article = _get_article(db, article_id)
    form = await request.form()
    apply_form(article, form)
    db.commit()
    return RedirectResponse(f"/articles/{article.id}", status_code=303)


@app.post("/articles/{article_id}/duplicate")
def duplicate_article(article_id: int, db: Session = Depends(get_db)):
    src = _get_article(db, article_id)
    copy = Article(
        title=f"{src.title} (Kopie)",
        description=src.description,
        category=src.category,
        condition=src.condition,
        status="Entwurf",  # Kopie startet als Entwurf
        purchase_cost=src.purchase_cost,
        listing_price=src.listing_price,
        shipping_method=src.shipping_method,
        shipping_cost=src.shipping_cost,
        fees=src.fees,
        tags=src.tags,
        # Verkaufs-/Käuferdaten und Links bewusst NICHT übernehmen
    )
    db.add(copy)
    db.commit()
    return RedirectResponse(f"/articles/{copy.id}/edit", status_code=303)


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

    # neues Bild ans Ende sortieren
    next_pos = max((img.position for img in article.images), default=-1) + 1
    db.add(ArticleImage(article_id=article.id, filename=filename, position=next_pos))
    db.commit()
    return RedirectResponse(f"/articles/{article_id}", status_code=303)


@app.post("/articles/{article_id}/images/{image_id}/main")
def set_main_image(article_id: int, image_id: int, db: Session = Depends(get_db)):
    article = _get_article(db, article_id)
    # ausgewähltes Bild nach vorne, Rest in bisheriger Reihenfolge dahinter
    ordered = sorted(article.images, key=lambda i: (i.position, i.id))
    ordered = [i for i in ordered if i.id == image_id] + [i for i in ordered if i.id != image_id]
    for pos, img in enumerate(ordered):
        img.position = pos
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
def export_csv(year: int | None = None, db: Session = Depends(get_db)):
    articles = db.scalars(select(Article).order_by(Article.id)).all()
    if year is not None:
        # Bei Jahresfilter nur die in diesem Jahr verkauften Artikel
        articles = [a for a in articles if a.sold_at and a.sold_at.year == year]

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([
        "ID", "Titel", "Kategorie", "Zustand", "Status", "Tags",
        "Einkaufskosten", "Angebotspreis", "Verkaufspreis",
        "Versandart", "Versandkosten", "Gebuehren", "Gewinn", "Marge %",
        "Verkauft ueber", "Kaeufer", "Zahlungsart",
        "Versanddienstleister", "Trackingnummer",
        "eBay-Link", "Kleinanzeigen-Link",
        "Angelegt", "Bestellt", "Versendet", "Verkauft",
    ])
    for a in articles:
        writer.writerow([
            a.id, a.title, a.category, a.condition, a.status, a.tags,
            f"{a.purchase_cost:.2f}", f"{a.listing_price:.2f}", f"{a.sold_price:.2f}",
            a.shipping_method, f"{a.shipping_cost:.2f}", f"{a.fees:.2f}",
            f"{a.profit:.2f}" if a.profit is not None else "",
            f"{a.margin:.1f}" if a.margin is not None else "",
            a.sale_platform, a.buyer_name, a.payment_method,
            a.tracking_carrier, a.tracking_number,
            a.ebay_url, a.kleinanzeigen_url,
            a.created_at.strftime("%d.%m.%Y") if a.created_at else "",
            a.order_date.strftime("%d.%m.%Y") if a.order_date else "",
            a.shipped_at.strftime("%d.%m.%Y") if a.shipped_at else "",
            a.sold_at.strftime("%d.%m.%Y") if a.sold_at else "",
        ])

    buffer.seek(0)
    # BOM voranstellen, damit Excel Umlaute korrekt anzeigt
    content = "﻿" + buffer.getvalue()
    suffix = f"_{year}" if year is not None else ""
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=warensystem_export{suffix}.csv"},
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
