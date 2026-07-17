"""Fachlogik und Hilfsfunktionen, die von mehreren Routern genutzt werden."""
import io
import uuid
import urllib.parse
from datetime import datetime, timezone

from fastapi import Request, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, ebay, images
from .models import Article, ArticleImage, Sale, StorageLocation, STATUSES, CONDITIONS, SHIPPING_METHODS, SHIPPING_OPTIONS, SHIPPING_PAYERS, SALE_PLATFORMS


def make_article_no(article_id: int) -> str:
    return f"{config.ARTICLE_NO_PREFIX}{article_id:05d}"


def assign_article_no(db: Session, article: Article) -> None:
    """Vergibt die interne Artikelnummer (benötigt eine vergebene ID)."""
    if article.id is None:
        db.flush()
    if not article.article_no:
        article.article_no = make_article_no(article.id)

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

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


NEW_CATEGORY = "__new__"


def _pick_category(data) -> str:
    """Kategorie aus dem Dropdown — oder die neu eingegebene."""
    category = (data.get("category") or "").strip()
    if category == NEW_CATEGORY:
        category = (data.get("category_new") or "").strip()
    return category


def apply_form(article: Article, data: dict) -> None:
    """Übernimmt Formulardaten in ein Article-Objekt."""
    article.title = (data.get("title") or "").strip() or "Ohne Titel"
    article.description = (data.get("description") or "").strip()
    article.category = _pick_category(data)
    article.condition = (data.get("condition") or "").strip()
    new_status = (data.get("status") or "Entwurf").strip()

    article.quantity = max(0, int(parse_float(data.get("quantity")) or 0))
    article.purchase_cost = parse_float(data.get("purchase_cost"))
    article.listing_price = parse_float(data.get("listing_price"))
    # Versand-Vorbelegung für kommende Verkäufe
    article.shipping_method = (data.get("shipping_method") or "").strip()
    article.shipping_cost = parse_float(data.get("shipping_cost"))
    article.shipping_payer = (data.get("shipping_payer") or "Käufer").strip()

    article.ebay_url = (data.get("ebay_url") or "").strip()
    article.ebay_item_id = (data.get("ebay_item_id") or "").strip()
    article.kleinanzeigen_url = (data.get("kleinanzeigen_url") or "").strip()
    article.offered_ebay = data.get("offered_ebay") is not None
    article.offered_kleinanzeigen = data.get("offered_kleinanzeigen") is not None

    # Schlagworte normalisieren (kommagetrennt, ohne Leereinträge)
    raw_tags = (data.get("tags") or "").split(",")
    article.tags = ", ".join(t.strip() for t in raw_tags if t.strip())

    article.note = (data.get("note") or "").strip()

    set_status(article, new_status)


def set_status(article: Article, new_status: str) -> None:
    """Setzt den Status des Artikels.

    Der Status ist eine Kennzeichnung des Angebots; Verkäufe und Bestand werden
    über die Verkaufshistorie bzw. `quantity` geführt.
    """
    article.status = new_status


def _free_storage_if_sold_out(article: Article) -> None:
    """Gibt den Lagerplatz frei, sobald der Bestand aufgebraucht ist."""
    if article.quantity <= 0:
        article.storage_area = ""
        article.storage_shelf = ""
        article.storage_bin = ""


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


def all_categories(db: Session) -> list[str]:
    """Alle vergebenen Kategorien (für Filter und Massenbearbeitung)."""
    rows = db.scalars(
        select(Article.category).where(Article.category != "").distinct()
    ).all()
    return sorted(rows)


def all_locations(db: Session) -> list[StorageLocation]:
    return db.scalars(
        select(StorageLocation).order_by(
            StorageLocation.area, StorageLocation.shelf, StorageLocation.bin
        )
    ).all()

MONTH_NAMES = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
               "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def _sold_years(db: Session) -> list[int]:
    """Alle Jahre, in denen etwas verkauft wurde (absteigend)."""
    rows = db.scalars(select(Sale.sold_at).where(Sale.sold_at.is_not(None))).all()
    return sorted({d.year for d in rows}, reverse=True)

SORT_COLUMNS = {
    "article_no": Article.article_no,
    "storage_area": Article.storage_area,
    "title": Article.title,
    "status": Article.status,
    "quantity": Article.quantity,
    "listing_price": Article.listing_price,
    "updated_at": Article.updated_at,
}

INTAKE_LIMIT = 100


def parse_intake_lines(text: str) -> list[tuple[str, float | None]]:
    """Zerlegt die Eingabe des Wareneingangs.

    Eine Position pro Zeile, optional mit erwartetem Verkaufspreis:
        Titel
        Titel | 49,90
    """
    items: list[tuple[str, float | None]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            title, _, price = line.partition("|")
            p = parse_float(price)
            items.append((title.strip() or "Ohne Titel", p if p > 0 else None))
        else:
            items.append((line, None))
    return items[:INTAKE_LIMIT]


def allocate_costs(total: float, weights: list[float] | None, count: int) -> list[float]:
    """Verteilt einen Gesamt-Einkaufspreis auf `count` Positionen.

    Mit `weights` (z.B. erwartete Verkaufspreise) anteilig, sonst gleichmäßig.
    Rundungsdifferenzen landen auf der letzten Position, damit die Summe exakt
    dem Gesamtpreis entspricht.
    """
    if count <= 0:
        return []
    if weights and len(weights) == count and sum(weights) > 0:
        gesamt = sum(weights)
        shares = [round(total * w / gesamt, 2) for w in weights]
    else:
        shares = [round(total / count, 2)] * count
    diff = round(total - sum(shares), 2)
    shares[-1] = round(shares[-1] + diff, 2)
    return shares


def _back_to_list(form, **extra) -> str:
    """Baut die Rücksprung-URL zur Artikelliste mit erhaltenen Filtern.

    `status` wird immer mitgegeben — sonst würde eine leere Auswahl
    ("Alle Status") auf den Standardfilter zurückfallen.
    """
    params = {
        "q": form.get("q", ""),
        "status": form.get("status", ""),
        "tag": form.get("tag", ""),
        "category": form.get("category", ""),
        "sort": form.get("sort", "article_no"),
        "dir": form.get("dir", "asc"),
    }
    params.update(extra)
    query = urllib.parse.urlencode(
        {k: v for k, v in params.items() if v != "" or k == "status"}
    )
    return f"/articles?{query}"

def _age_days(since: datetime | None) -> int | None:
    """Tage seit einem Zeitpunkt (naive Werte werden als UTC behandelt)."""
    if since is None:
        return None
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - since).days

def _form_context(request: Request, article: Article | None, db: Session, error: str = "") -> dict:
    return {
        "request": request,
        "article": article,
        "statuses": STATUSES,
        "conditions": CONDITIONS,
        "categories": all_categories(db),
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

def _delete_image_files(filename: str) -> None:
    """Entfernt Bilddatei und zugehöriges Thumbnail."""
    for name in (filename, images.thumb_name(filename)):
        (config.UPLOAD_DIR / name).unlink(missing_ok=True)


def _download_item_images(db: Session, article: Article, image_urls: list[str]) -> None:
    """Lädt eBay-Bilder herunter und legt sie verkleinert + mit Thumbnail ab."""
    for pos, img_url in enumerate(image_urls):
        data = ebay.fetch_image(img_url)
        if not data:
            continue
        try:
            filename = images.process_upload(data, config.UPLOAD_DIR, uuid.uuid4().hex)
        except Exception:
            continue      # unlesbares Bild einfach überspringen
        db.add(ArticleImage(article_id=article.id, filename=filename, position=pos))


def _create_article_from_item(db: Session, item: dict, fallback_url: str = "") -> Article:
    """Legt aus einem geladenen eBay-Item einen Entwurf an (inkl. Bilder)."""
    article = Article(
        title=item["title"] or "eBay-Import",
        description=item["description"],
        condition=item["condition"],
        status="Entwurf",
        quantity=item.get("quantity", 1),   # Stückzahl aus dem Inserat
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

BULK_IMPORT_LIMIT = 100

def _get_article(db: Session, article_id: int) -> Article:
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    return article

def _article_url(article_id: int) -> str:
    return f"{config.BASE_URL}/articles/{article_id}"


def make_qr_svg(data: str) -> str:
    """Erzeugt einen QR-Code als SVG-String (ohne Bild-Abhängigkeit)."""
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")

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

def _sync_stock_status(article: Article) -> None:
    """Hält Status/Lagerplatz konsistent zum Bestand (nach Verkauf/Korrektur)."""
    if article.quantity <= 0:
        article.status = "Verkauft"
        _free_storage_if_sold_out(article)
    elif article.status in ("Verkauft", "Archiviert"):
        # Bestand wieder da (z.B. Verkauf korrigiert/gelöscht) -> wieder anbieten
        article.status = "Angeboten"


def _get_sale(db: Session, sale_id: int) -> Sale:
    sale = db.get(Sale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Verkauf nicht gefunden")
    return sale
