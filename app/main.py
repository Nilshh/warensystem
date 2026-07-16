"""Warenwirtschaftssystem — FastAPI-App."""
import asyncio
import csv
import io
import logging
import urllib.parse
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import qrcode
import qrcode.image.svg
from fastapi import FastAPI, Depends, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse, HTMLResponse, Response
from markupsafe import Markup
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import backup, config, ebay
from .database import Base, engine, get_db, SessionLocal
from .migrations import run_migrations
from .models import (
    Article, ArticleImage, StorageLocation,
    STATUSES, CONDITIONS, SHIPPING_METHODS, SHIPPING_OPTIONS,
    SHIPPING_PAYERS, SALE_PLATFORMS,
)

log = logging.getLogger("warensystem")

# Tabellen anlegen und fehlende Spalten nachziehen (leichtgewichtige Migration)
Base.metadata.create_all(engine)
run_migrations(engine)


def make_article_no(article_id: int) -> str:
    return f"{config.ARTICLE_NO_PREFIX}{article_id:05d}"


def assign_article_no(db: Session, article: Article) -> None:
    """Vergibt die interne Artikelnummer (benötigt eine vergebene ID)."""
    if article.id is None:
        db.flush()
    if not article.article_no:
        article.article_no = make_article_no(article.id)


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


backfill_article_numbers()


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


backfill_storage_locations()


def archive_old_sales() -> int:
    """Setzt verkaufte Artikel nach ARCHIVE_AFTER_DAYS auf 'Archiviert'.

    Das Verkaufsdatum bleibt erhalten, damit die Statistik stimmt.
    Gibt die Anzahl archivierter Artikel zurück.
    """
    days = config.ARCHIVE_AFTER_DAYS
    if days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    db = SessionLocal()
    try:
        candidates = db.scalars(
            select(Article).where(
                Article.status == "Verkauft", Article.sold_at.is_not(None)
            )
        ).all()
        n = 0
        for a in candidates:
            sold = a.sold_at
            if sold.tzinfo is None:            # naive Werte als UTC behandeln
                sold = sold.replace(tzinfo=timezone.utc)
            if sold <= cutoff:
                a.status = "Archiviert"        # sold_at bleibt erhalten
                n += 1
        if n:
            db.commit()
            log.info("Auto-Archivierung: %d Artikel archiviert.", n)
        return n
    finally:
        db.close()


async def _archive_loop():
    """Prüft periodisch auf zu archivierende Verkäufe (alle 6 Stunden)."""
    while True:
        try:
            await asyncio.to_thread(archive_old_sales)
        except Exception:  # Loop niemals sterben lassen
            log.exception("Auto-Archivierung fehlgeschlagen")
        await asyncio.sleep(6 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_archive_loop()) if config.ARCHIVE_AFTER_DAYS > 0 else None
    try:
        yield
    finally:
        if task:
            task.cancel()


app = FastAPI(title="Warenwirtschaftssystem", lifespan=lifespan)

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
    article.shipping_payer = (data.get("shipping_payer") or "Käufer").strip()
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
    article.buyer_address = (data.get("buyer_address") or "").strip()
    article.payment_method = (data.get("payment_method") or "").strip()
    article.tracking_carrier = (data.get("tracking_carrier") or "").strip()
    article.tracking_number = (data.get("tracking_number") or "").strip()
    article.order_date = parse_date(data.get("order_date"))
    article.shipped_at = parse_date(data.get("shipped_at"))
    article.note = (data.get("note") or "").strip()

    set_status(article, new_status)


def set_status(article: Article, new_status: str) -> None:
    """Setzt den Status und pflegt das Verkaufsdatum automatisch.

    - Wechsel auf "Verkauft" stempelt das Verkaufsdatum (falls noch keins).
    - Zurück in den Verkaufsprozess (Entwurf/Angeboten/Reserviert) verwirft den
      Verkauf und löscht das Datum.
    - "Archiviert" behält das Verkaufsdatum -> archivierte Verkäufe zählen weiter.
    """
    if new_status == "Verkauft" and article.sold_at is None:
        article.sold_at = datetime.now(timezone.utc)
        # Lagerplatz wird beim Verkauf frei
        article.storage_area = ""
        article.storage_shelf = ""
        article.storage_bin = ""
    elif new_status in ("Entwurf", "Angeboten", "Reserviert"):
        article.sold_at = None
    article.status = new_status


def _set_article_storage(article: Article, loc: StorageLocation | None) -> None:
    """Setzt/leert den Lagerplatz eines Artikels anhand eines verwalteten Lagerorts."""
    article.storage_area = loc.area if loc else ""
    article.storage_shelf = loc.shelf if loc else ""
    article.storage_bin = loc.bin if loc else ""


def apply_storage(db: Session, article: Article, data) -> None:
    """Übernimmt die im Dropdown gewählte Lagerplatz-ID auf den Artikel."""
    loc_id = data.get("storage_location_id")
    loc = None
    if loc_id and str(loc_id).isdigit():
        loc = db.get(StorageLocation, int(loc_id))
    _set_article_storage(article, loc)


def current_location_id(db: Session, article: Article | None) -> int | None:
    """Findet die Lagerort-ID zum aktuellen Lagerplatz eines Artikels (für Vorauswahl)."""
    if not article or not article.storage_location:
        return None
    loc = db.scalar(
        select(StorageLocation).where(
            StorageLocation.area == article.storage_area,
            StorageLocation.shelf == article.storage_shelf,
            StorageLocation.bin == article.storage_bin,
        )
    )
    return loc.id if loc else None


def all_locations(db: Session) -> list[StorageLocation]:
    return db.scalars(
        select(StorageLocation).order_by(
            StorageLocation.area, StorageLocation.shelf, StorageLocation.bin
        )
    ).all()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
MONTH_NAMES = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
               "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def _sold_years(db: Session) -> list[int]:
    """Alle Jahre, in denen etwas verkauft wurde (absteigend)."""
    rows = db.scalars(
        select(Article.sold_at).where(Article.sold_at.is_not(None))
    ).all()
    return sorted({d.year for d in rows}, reverse=True)


@app.get("/", response_class=HTMLResponse)
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

    sold_all = db.scalars(select(Article).where(Article.sold_at.is_not(None))).all()
    # Nach gewähltem Jahr filtern (für Kennzahlen + Diagramm) — inkl. archivierter Verkäufe
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
        "restored": restored,
        "error": error,
    }
    return templates.TemplateResponse("dashboard.html", ctx)


# ---------------------------------------------------------------------------
# Artikelliste
# ---------------------------------------------------------------------------
SORT_COLUMNS = {
    "article_no": Article.article_no,
    "storage_area": Article.storage_area,
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
    updated: int = 0,
    stored: int = 0,
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
            | Article.storage_area.ilike(like)
            | Article.storage_shelf.ilike(like)
            | Article.storage_bin.ilike(like)
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
        "updated": updated,
        "stored": stored,
        "storage_locations": all_locations(db),
    }
    return templates.TemplateResponse("articles.html", ctx)


@app.post("/articles/bulk-status")
async def bulk_status(request: Request, db: Session = Depends(get_db)):
    """Ändert den Status mehrerer ausgewählter Artikel auf einmal."""
    form = await request.form()
    new_status = (form.get("new_status") or "").strip()
    ids = [int(i) for i in form.getlist("ids") if str(i).isdigit()]

    updated = 0
    if new_status in STATUSES and ids:
        articles = db.scalars(select(Article).where(Article.id.in_(ids))).all()
        for a in articles:
            set_status(a, new_status)
            updated += 1
        db.commit()

    # aktuelle Filter/Sortierung beim Zurückspringen erhalten
    params = {
        "q": form.get("q", ""),
        "status": form.get("status", ""),
        "tag": form.get("tag", ""),
        "sort": form.get("sort", "updated_at"),
        "dir": form.get("dir", "desc"),
        "updated": updated,
    }
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v != ""})
    return RedirectResponse(f"/articles?{query}", status_code=303)


@app.post("/articles/bulk-labels", response_class=HTMLResponse)
async def bulk_labels(request: Request, db: Session = Depends(get_db)):
    """Druckseite mit den QR-Etiketten mehrerer ausgewählter Artikel."""
    form = await request.form()
    ids = [int(i) for i in form.getlist("ids") if str(i).isdigit()]
    articles = (
        db.scalars(
            select(Article).where(Article.id.in_(ids)).order_by(Article.article_no)
        ).all()
        if ids else []
    )
    labels = [
        {
            "article": a,
            "url": _article_url(a.id),
            "qr_svg": Markup(make_qr_svg(_article_url(a.id))),
        }
        for a in articles
    ]
    return templates.TemplateResponse(
        "labels_bulk.html", {"request": request, "labels": labels}
    )


@app.post("/articles/bulk-storage")
async def bulk_storage(request: Request, db: Session = Depends(get_db)):
    """Setzt den Lagerplatz mehrerer ausgewählter Artikel auf einmal."""
    form = await request.form()
    ids = [int(i) for i in form.getlist("ids") if str(i).isdigit()]
    loc_id = form.get("storage_location_id")
    loc = db.get(StorageLocation, int(loc_id)) if loc_id and str(loc_id).isdigit() else None

    stored = 0
    if ids:
        for a in db.scalars(select(Article).where(Article.id.in_(ids))).all():
            _set_article_storage(a, loc)
            stored += 1
        db.commit()

    params = {
        "q": form.get("q", ""), "status": form.get("status", ""),
        "tag": form.get("tag", ""), "sort": form.get("sort", "updated_at"),
        "dir": form.get("dir", "desc"), "stored": stored,
    }
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v != ""})
    return RedirectResponse(f"/articles?{query}", status_code=303)


# ---------------------------------------------------------------------------
# Artikel anlegen
# ---------------------------------------------------------------------------
def _form_context(request: Request, article: Article | None, db: Session, error: str = "") -> dict:
    return {
        "request": request,
        "article": article,
        "statuses": STATUSES,
        "conditions": CONDITIONS,
        "shipping_methods": SHIPPING_METHODS,
        "shipping_options": SHIPPING_OPTIONS,
        "shipping_payers": SHIPPING_PAYERS,
        "sale_platforms": SALE_PLATFORMS,
        "storage_locations": all_locations(db),
        "current_storage_id": current_location_id(db, article),
        "fee_percent": config.DEFAULT_EBAY_FEE_PERCENT,
        "ebay_import_enabled": ebay.import_supported(),
        "error": error,
    }


@app.get("/articles/new", response_class=HTMLResponse)
def new_article(request: Request, error: str = "", db: Session = Depends(get_db)):
    return templates.TemplateResponse("article_form.html", _form_context(request, None, db, error))


@app.post("/articles/new")
async def create_article(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    article = Article()
    apply_form(article, form)
    apply_storage(db, article, form)
    db.add(article)
    assign_article_no(db, article)
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
    assign_article_no(db, article)
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
def article_detail(
    article_id: int, request: Request,
    msg: str = "", error: str = "",
    db: Session = Depends(get_db),
):
    article = _get_article(db, article_id)
    return templates.TemplateResponse(
        "article_detail.html",
        {
            "request": request, "article": article,
            "msg": msg, "error": error,
            "ebay_refresh_enabled": ebay.import_supported()
            and bool(article.ebay_item_id or article.ebay_url),
        },
    )


@app.get("/articles/{article_id}/sell", response_class=HTMLResponse)
def sell_form(article_id: int, request: Request, db: Session = Depends(get_db)):
    """Geführtes Formular zum Erfassen eines Verkaufs."""
    article = _get_article(db, article_id)
    # sinnvolle Vorbelegung der Plattform
    default_platform = article.sale_platform
    if not default_platform:
        if article.offered_ebay:
            default_platform = "eBay"
        elif article.offered_kleinanzeigen:
            default_platform = "Kleinanzeigen"
    return templates.TemplateResponse(
        "sell_form.html",
        {
            "request": request, "article": article,
            "shipping_methods": SHIPPING_METHODS,
            "shipping_options": SHIPPING_OPTIONS,
            "shipping_payers": SHIPPING_PAYERS,
            "sale_platforms": SALE_PLATFORMS,
            "fee_percent": config.DEFAULT_EBAY_FEE_PERCENT,
            "default_platform": default_platform,
        },
    )


@app.post("/articles/{article_id}/sell")
async def sell_submit(article_id: int, request: Request, db: Session = Depends(get_db)):
    article = _get_article(db, article_id)
    form = await request.form()

    article.sold_price = parse_float(form.get("sold_price"))
    article.sale_platform = (form.get("sale_platform") or "").strip()
    article.buyer_name = (form.get("buyer_name") or "").strip()
    article.buyer_address = (form.get("buyer_address") or "").strip()
    article.payment_method = (form.get("payment_method") or "").strip()
    article.shipping_method = (form.get("shipping_method") or "").strip()
    article.shipping_cost = parse_float(form.get("shipping_cost"))
    article.shipping_payer = (form.get("shipping_payer") or "Käufer").strip()
    article.fees = parse_float(form.get("fees"))
    article.tracking_carrier = (form.get("tracking_carrier") or "").strip()
    article.tracking_number = (form.get("tracking_number") or "").strip()
    article.order_date = parse_date(form.get("order_date"))
    article.shipped_at = parse_date(form.get("shipped_at"))
    article.note = (form.get("note") or "").strip()

    set_status(article, "Verkauft")
    db.commit()

    note = urllib.parse.quote(f"Verkauf erfasst. Gewinn: {format_eur(article.profit)}.")
    return RedirectResponse(f"/articles/{article_id}?msg={note}", status_code=303)


@app.post("/articles/{article_id}/refresh-ebay")
def refresh_from_ebay(article_id: int, db: Session = Depends(get_db)):
    """Aktualisiert einen Artikel mit frischen Daten aus dem eBay-Inserat."""
    article = _get_article(db, article_id)
    source = article.ebay_item_id or article.ebay_url
    if not source:
        msg = urllib.parse.quote("Kein eBay-Bezug hinterlegt (Artikelnummer/Link fehlt).")
        return RedirectResponse(f"/articles/{article_id}?error={msg}", status_code=303)

    try:
        item = ebay.fetch_item(source)
    except ebay.EbayError as e:
        msg = urllib.parse.quote(str(e))
        return RedirectResponse(f"/articles/{article_id}?error={msg}", status_code=303)

    old_price = article.listing_price
    if item["title"]:
        article.title = item["title"]
    if item["condition"]:
        article.condition = item["condition"]
    if item["description"]:
        article.description = item["description"]
    if item["price"] and item["price"] > 0:
        article.listing_price = item["price"]
    if item["item_web_url"]:
        article.ebay_url = item["item_web_url"]
    if item["ebay_item_id"]:
        article.ebay_item_id = item["ebay_item_id"]
    db.commit()

    if abs(article.listing_price - old_price) > 0.001:
        note = f"Von eBay aktualisiert. Preis {format_eur(old_price)} → {format_eur(article.listing_price)}."
    else:
        note = "Von eBay aktualisiert. Preis unverändert."
    return RedirectResponse(f"/articles/{article_id}?msg={urllib.parse.quote(note)}", status_code=303)


def _article_url(article_id: int) -> str:
    return f"{config.BASE_URL}/articles/{article_id}"


def make_qr_svg(data: str) -> str:
    """Erzeugt einen QR-Code als SVG-String (ohne Bild-Abhängigkeit)."""
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


@app.get("/articles/{article_id}/qr.svg")
def article_qr(article_id: int, db: Session = Depends(get_db)):
    _get_article(db, article_id)  # 404, falls es den Artikel nicht gibt
    svg = make_qr_svg(_article_url(article_id))
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/articles/{article_id}/label", response_class=HTMLResponse)
def article_label(article_id: int, request: Request, db: Session = Depends(get_db)):
    """Druckbares Etikett mit QR-Code, Artikelnummer und Titel."""
    article = _get_article(db, article_id)
    return templates.TemplateResponse(
        "label.html",
        {
            "request": request,
            "article": article,
            "url": _article_url(article_id),
            "qr_svg": Markup(make_qr_svg(_article_url(article_id))),
        },
    )


# ---------------------------------------------------------------------------
# Lager / Lagerorte
# ---------------------------------------------------------------------------
def format_storage(area: str, shelf: str, bin_: str) -> str:
    parts = []
    if area:
        parts.append(area)
    if shelf:
        parts.append(f"Regal {shelf}")
    if bin_:
        parts.append(f"Fach {bin_}")
    return ", ".join(parts)


def _storage_query(area: str, shelf: str, bin_: str) -> str:
    return urllib.parse.urlencode({"area": area, "shelf": shelf, "bin": bin_})


def _storage_url(area: str, shelf: str, bin_: str) -> str:
    return f"{config.BASE_URL}/storage/location?{_storage_query(area, shelf, bin_)}"


@app.get("/storage", response_class=HTMLResponse)
def storage_overview(request: Request, error: str = "", db: Session = Depends(get_db)):
    """Übersicht der verwalteten Lagerorte mit Inhalt; Lagerorte hier anlegen."""
    locations = []
    for loc in all_locations(db):
        items = db.scalars(
            select(Article).where(
                Article.storage_area == loc.area,
                Article.storage_shelf == loc.shelf,
                Article.storage_bin == loc.bin,
            ).order_by(Article.article_no)
        ).all()
        locations.append({
            "id": loc.id, "area": loc.area, "shelf": loc.shelf, "bin": loc.bin,
            "label": loc.label, "articles": items,
        })
    return templates.TemplateResponse(
        "storage_overview.html", {"request": request, "locations": locations, "error": error}
    )


@app.post("/storage/new")
async def storage_new(
    area: str = Form(""), shelf: str = Form(""), bin: str = Form(""),
    db: Session = Depends(get_db),
):
    area, shelf, bin = area.strip(), shelf.strip(), bin.strip()
    if not (area or shelf or bin):
        msg = urllib.parse.quote("Bitte mindestens Bereich, Regal oder Fach angeben.")
        return RedirectResponse(f"/storage?error={msg}", status_code=303)
    # Duplikat vermeiden
    exists = db.scalar(
        select(StorageLocation).where(
            StorageLocation.area == area, StorageLocation.shelf == shelf, StorageLocation.bin == bin
        )
    )
    if not exists:
        db.add(StorageLocation(area=area, shelf=shelf, bin=bin))
        db.commit()
    return RedirectResponse("/storage", status_code=303)


@app.post("/storage/{loc_id}/edit")
async def storage_edit(
    loc_id: int, area: str = Form(""), shelf: str = Form(""), bin: str = Form(""),
    db: Session = Depends(get_db),
):
    loc = db.get(StorageLocation, loc_id)
    if not loc:
        return RedirectResponse("/storage", status_code=303)
    area, shelf, bin = area.strip(), shelf.strip(), bin.strip()
    if not (area or shelf or bin):
        msg = urllib.parse.quote("Bitte mindestens Bereich, Regal oder Fach angeben.")
        return RedirectResponse(f"/storage?error={msg}", status_code=303)
    # Dublette vermeiden (anderer Lagerplatz mit denselben Werten)
    other = db.scalar(
        select(StorageLocation).where(
            StorageLocation.area == area, StorageLocation.shelf == shelf,
            StorageLocation.bin == bin, StorageLocation.id != loc_id,
        )
    )
    if other:
        msg = urllib.parse.quote("Es gibt bereits einen Lagerplatz mit diesen Werten.")
        return RedirectResponse(f"/storage?error={msg}", status_code=303)

    # zugeordnete Artikel mitziehen
    old = (loc.area, loc.shelf, loc.bin)
    if old != (area, shelf, bin):
        for a in db.scalars(
            select(Article).where(
                Article.storage_area == old[0],
                Article.storage_shelf == old[1],
                Article.storage_bin == old[2],
            )
        ).all():
            a.storage_area, a.storage_shelf, a.storage_bin = area, shelf, bin
        loc.area, loc.shelf, loc.bin = area, shelf, bin
        db.commit()
    return RedirectResponse("/storage", status_code=303)


@app.post("/storage/{loc_id}/delete")
def storage_delete(loc_id: int, db: Session = Depends(get_db)):
    loc = db.get(StorageLocation, loc_id)
    if loc:
        count = db.scalar(
            select(func.count(Article.id)).where(
                Article.storage_area == loc.area,
                Article.storage_shelf == loc.shelf,
                Article.storage_bin == loc.bin,
            )
        ) or 0
        if count > 0:
            msg = urllib.parse.quote(
                f"Lagerplatz {loc.label} ist nicht leer ({count} Artikel) und kann nicht gelöscht werden."
            )
            return RedirectResponse(f"/storage?error={msg}", status_code=303)
        db.delete(loc)
        db.commit()
    return RedirectResponse("/storage", status_code=303)


@app.get("/storage/location", response_class=HTMLResponse)
def storage_location(
    request: Request, area: str = "", shelf: str = "", bin: str = "",
    db: Session = Depends(get_db),
):
    """Inhalt eines bestimmten Lagerorts (Ziel der Lager-QR-Codes)."""
    articles = db.scalars(
        select(Article).where(
            Article.storage_area == area,
            Article.storage_shelf == shelf,
            Article.storage_bin == bin,
        ).order_by(Article.article_no)
    ).all()
    label = format_storage(area, shelf, bin)
    return templates.TemplateResponse(
        "storage_location.html",
        {
            "request": request, "articles": articles, "label": label,
            "area": area, "shelf": shelf, "bin": bin,
            "query": _storage_query(area, shelf, bin),
        },
    )


@app.get("/storage/qr.svg")
def storage_qr(area: str = "", shelf: str = "", bin: str = ""):
    svg = make_qr_svg(_storage_url(area, shelf, bin))
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/storage/label", response_class=HTMLResponse)
def storage_label(
    request: Request, area: str = "", shelf: str = "", bin: str = "",
):
    """Druckbares Etikett für ein Lagerfach/eine Kiste."""
    label = format_storage(area, shelf, bin)
    return templates.TemplateResponse(
        "storage_label.html",
        {
            "request": request, "label": label,
            "query": _storage_query(area, shelf, bin),
            "url": _storage_url(area, shelf, bin),
            "qr_svg": Markup(make_qr_svg(_storage_url(area, shelf, bin))),
        },
    )


@app.get("/articles/{article_id}/lieferschein", response_class=HTMLResponse)
def lieferschein(article_id: int, request: Request, db: Session = Depends(get_db)):
    """Druckbarer Lieferschein/Packzettel für einen Artikel."""
    article = _get_article(db, article_id)
    seller = {
        "name": config.SELLER_NAME,
        "address": config.SELLER_ADDRESS.replace("\\n", "\n"),
        "email": config.SELLER_EMAIL,
        "phone": config.SELLER_PHONE,
    }
    date = article.shipped_at or article.sold_at or datetime.now(timezone.utc)
    return templates.TemplateResponse(
        "lieferschein.html",
        {"request": request, "article": article, "seller": seller, "date": date},
    )


# ---------------------------------------------------------------------------
# Artikel bearbeiten
# ---------------------------------------------------------------------------
@app.get("/articles/{article_id}/edit", response_class=HTMLResponse)
def edit_article(article_id: int, request: Request, db: Session = Depends(get_db)):
    article = _get_article(db, article_id)
    return templates.TemplateResponse("article_form.html", _form_context(request, article, db))


@app.post("/articles/{article_id}/edit")
async def update_article(article_id: int, request: Request, db: Session = Depends(get_db)):
    article = _get_article(db, article_id)
    form = await request.form()
    apply_form(article, form)
    apply_storage(db, article, form)
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
    assign_article_no(db, copy)
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
        "Artikelnr", "ID", "Titel", "Kategorie", "Zustand", "Status", "Lagerplatz", "Tags",
        "Einkaufskosten", "Angebotspreis", "Verkaufspreis",
        "Versandart", "Versandkosten", "Versand zahlt", "Gebuehren", "Gewinn", "Marge %",
        "Verkauft ueber", "Kaeufer", "Zahlungsart",
        "Versanddienstleister", "Trackingnummer", "Notiz",
        "eBay-Link", "Kleinanzeigen-Link",
        "Angelegt", "Bestellt", "Versendet", "Verkauft",
    ])
    for a in articles:
        writer.writerow([
            a.article_no, a.id, a.title, a.category, a.condition, a.status, a.storage_location, a.tags,
            f"{a.purchase_cost:.2f}", f"{a.listing_price:.2f}", f"{a.sold_price:.2f}",
            a.shipping_method, f"{a.shipping_cost:.2f}", a.shipping_payer, f"{a.fees:.2f}",
            f"{a.profit:.2f}" if a.profit is not None else "",
            f"{a.margin:.1f}" if a.margin is not None else "",
            a.sale_platform, a.buyer_name, a.payment_method,
            a.tracking_carrier, a.tracking_number, a.note,
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


# ---------------------------------------------------------------------------
# Backup & Restore
# ---------------------------------------------------------------------------
@app.get("/backup.zip")
def backup_download():
    data = backup.create_backup()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=warensystem-backup-{stamp}.zip"},
    )


@app.post("/restore")
async def restore_upload(file: UploadFile = File(...)):
    data = await file.read()
    try:
        backup.restore_backup(data)
        run_migrations(engine)  # ältere Backups ggf. auf aktuelles Schema heben
    except ValueError as e:
        return RedirectResponse(f"/?error={urllib.parse.quote(str(e))}", status_code=303)
    return RedirectResponse("/?restored=1", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok"}
