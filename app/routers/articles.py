"""Routen: articles."""
import uuid
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, Response
from markupsafe import Markup
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import config, ebay, images
from ..database import get_db
from ..models import Article, ArticleImage, Sale, StorageLocation, STATUSES, CONDITIONS, SHIPPING_METHODS, SHIPPING_OPTIONS, SHIPPING_PAYERS, SALE_PLATFORMS
from ..services import assign_article_no, ALLOWED_IMAGE_EXT, parse_float, parse_date, apply_form, set_status, _set_article_storage, apply_storage, all_categories, all_locations, SORT_COLUMNS, INTAKE_LIMIT, parse_intake_lines, allocate_costs, _back_to_list, _form_context, _delete_image_files, _create_article_from_item, BULK_IMPORT_LIMIT, _get_article, _article_url, make_qr_svg, _sync_stock_status, advance_fulfillment
from ..web import templates, format_eur

router = APIRouter()


@router.get("/articles", response_class=HTMLResponse)
def list_articles(
    request: Request,
    q: str = "",
    status: str = "Angeboten",   # Standardfilter; "?status=" (leer) zeigt alle
    tag: str = "",
    category: str = "",
    sort: str = "article_no",
    dir: str = "asc",
    updated: int = 0,
    stored: int = 0,
    categorized: int = 0,
    db: Session = Depends(get_db),
):
    column = SORT_COLUMNS.get(sort, Article.article_no)
    order = column.asc() if dir == "asc" else column.desc()

    # Verkäufe und Bilder gleich mitladen — die Liste zeigt Umsatz/Gewinn und
    # das Hauptbild je Zeile (sonst je Artikel zwei Extra-Abfragen).
    stmt = (
        select(Article)
        .options(selectinload(Article.sales), selectinload(Article.images))
        .order_by(order)
    )
    if status:
        stmt = stmt.where(Article.status == status)
    if category:
        stmt = stmt.where(Article.category == category)
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
        "active_category": category,
        "sort": sort,
        "dir": dir,
        "updated": updated,
        "stored": stored,
        "categorized": categorized,
        "storage_locations": all_locations(db),
        "categories": all_categories(db),
    }
    return templates.TemplateResponse("articles.html", ctx)

@router.post("/articles/bulk-status")
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

    return RedirectResponse(_back_to_list(form, updated=updated), status_code=303)

@router.get("/intake", response_class=HTMLResponse)
def intake_form(request: Request, error: str = "", db: Session = Depends(get_db)):
    """Wareneingang: Konvolut kaufen und in Einzelartikel aufteilen."""
    return templates.TemplateResponse(
        "intake.html",
        {
            "request": request, "error": error,
            "conditions": CONDITIONS,
            "categories": all_categories(db),
            "storage_locations": all_locations(db),
            "intake_limit": INTAKE_LIMIT,
        },
    )


@router.post("/intake", response_class=HTMLResponse)
async def intake_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    total = parse_float(form.get("total_cost"))
    items = parse_intake_lines(form.get("items", ""))

    if not items:
        msg = urllib.parse.quote("Bitte mindestens eine Position angeben (eine pro Zeile).")
        return RedirectResponse(f"/intake?error={msg}", status_code=303)

    prices = [p for _, p in items]
    # Nur anteilig verteilen, wenn für ALLE Positionen ein Preis angegeben wurde
    weights = prices if all(p is not None for p in prices) else None
    shares = allocate_costs(total, weights, len(items))
    verteilung = "anteilig nach erwartetem Verkaufspreis" if weights else "gleichmäßig"

    loc = None
    loc_id = form.get("storage_location_id")
    if loc_id and str(loc_id).isdigit():
        loc = db.get(StorageLocation, int(loc_id))

    category = (form.get("category") or "").strip()
    condition = (form.get("condition") or "").strip()
    tags = (form.get("tags") or "").strip()
    created = []
    for (title, price), share in zip(items, shares):
        article = Article(
            title=title,
            status="Entwurf",
            quantity=1,
            purchase_cost=share,
            listing_price=price or 0.0,
            category=category,
            condition=condition,
            tags=tags,
        )
        _set_article_storage(article, loc)
        db.add(article)
        db.flush()
        assign_article_no(db, article)
        created.append(article)
    db.commit()

    return templates.TemplateResponse(
        "intake_result.html",
        {
            "request": request, "articles": created,
            "total": total, "verteilung": verteilung,
        },
    )

@router.post("/articles/bulk-category")
async def bulk_category(request: Request, db: Session = Depends(get_db)):
    """Setzt die Kategorie mehrerer ausgewählter Artikel auf einmal."""
    form = await request.form()
    ids = [int(i) for i in form.getlist("ids") if str(i).isdigit()]
    category = (form.get("new_category") or "").strip()

    categorized = 0
    if ids:
        for a in db.scalars(select(Article).where(Article.id.in_(ids))).all():
            a.category = category      # leer = Kategorie entfernen
            categorized += 1
        db.commit()

    return RedirectResponse(_back_to_list(form, categorized=categorized), status_code=303)


@router.post("/articles/bulk-labels", response_class=HTMLResponse)
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


@router.post("/articles/bulk-storage")
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

    return RedirectResponse(_back_to_list(form, stored=stored), status_code=303)


# ---------------------------------------------------------------------------
# Artikel anlegen

@router.get("/articles/new", response_class=HTMLResponse)
def new_article(request: Request, error: str = "", db: Session = Depends(get_db)):
    return templates.TemplateResponse("article_form.html", _form_context(request, None, db, error))


@router.post("/articles/new")
async def create_article(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    article = Article()
    apply_form(article, form)
    apply_storage(db, article, form)
    db.add(article)
    assign_article_no(db, article)
    db.commit()
    return RedirectResponse(f"/articles/{article.id}", status_code=303)

@router.post("/articles/import-ebay")
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


@router.post("/articles/import-ebay-bulk", response_class=HTMLResponse)
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

@router.get("/articles/{article_id}", response_class=HTMLResponse)
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


@router.get("/articles/{article_id}/sell", response_class=HTMLResponse)
def sell_form(article_id: int, request: Request, db: Session = Depends(get_db)):
    """Geführtes Formular zum Erfassen eines Verkaufs."""
    article = _get_article(db, article_id)
    # sinnvolle Vorbelegung der Plattform aus den Angebots-Häkchen
    default_platform = ""
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


@router.post("/articles/{article_id}/sell")
async def sell_submit(article_id: int, request: Request, db: Session = Depends(get_db)):
    article = _get_article(db, article_id)
    form = await request.form()

    qty = max(1, int(parse_float(form.get("quantity")) or 1))
    if qty > article.quantity:
        msg = urllib.parse.quote(
            f"Nur noch {article.quantity} Stück auf Bestand — Verkauf nicht erfasst."
        )
        return RedirectResponse(f"/articles/{article_id}?error={msg}", status_code=303)

    sale = Sale(
        article_id=article.id,
        quantity=qty,
        sold_price=parse_float(form.get("sold_price")),
        unit_purchase_cost=article.purchase_cost,   # Snapshot des Einkaufspreises
        fees=parse_float(form.get("fees")),
        shipping_method=(form.get("shipping_method") or "").strip(),
        shipping_cost=parse_float(form.get("shipping_cost")),
        shipping_payer=(form.get("shipping_payer") or "Käufer").strip(),
        sale_platform=(form.get("sale_platform") or "").strip(),
        buyer_name=(form.get("buyer_name") or "").strip(),
        buyer_address=(form.get("buyer_address") or "").strip(),
        payment_method=(form.get("payment_method") or "").strip(),
        # Dienstleister ergibt sich aus der Versandart (siehe Sale.carrier_label)
        tracking_number=(form.get("tracking_number") or "").strip(),
        note=(form.get("note") or "").strip(),
        order_date=parse_date(form.get("order_date")),
        shipped_at=parse_date(form.get("shipped_at")),
        sold_at=datetime.now(timezone.utc),
    )
    # Abwicklung: schon beim Erfassen versendet? -> gleich 'Versendet'
    if sale.shipped_at:
        advance_fulfillment(sale, "Versendet")
    db.add(sale)

    # Bestand reduzieren; bei Ausverkauf Status setzen und Lagerplatz freigeben
    article.quantity -= qty
    _sync_stock_status(article)
    db.commit()

    rest = f" Restbestand: {article.quantity}." if article.quantity > 0 else " Artikel ist jetzt ausverkauft."
    note = urllib.parse.quote(f"Verkauf erfasst. Gewinn: {format_eur(sale.profit)}.{rest}")
    return RedirectResponse(f"/articles/{article_id}?msg={note}", status_code=303)


@router.post("/articles/{article_id}/refresh-ebay")
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

@router.get("/articles/{article_id}/qr.svg")
def article_qr(article_id: int, db: Session = Depends(get_db)):
    _get_article(db, article_id)  # 404, falls es den Artikel nicht gibt
    svg = make_qr_svg(_article_url(article_id))
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/articles/{article_id}/label", response_class=HTMLResponse)
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

@router.get("/articles/{article_id}/edit", response_class=HTMLResponse)
def edit_article(article_id: int, request: Request, db: Session = Depends(get_db)):
    article = _get_article(db, article_id)
    return templates.TemplateResponse("article_form.html", _form_context(request, article, db))


@router.post("/articles/{article_id}/edit")
async def update_article(article_id: int, request: Request, db: Session = Depends(get_db)):
    article = _get_article(db, article_id)
    form = await request.form()
    apply_form(article, form)
    apply_storage(db, article, form)
    db.commit()
    return RedirectResponse(f"/articles/{article.id}", status_code=303)


@router.post("/articles/{article_id}/duplicate")
def duplicate_article(article_id: int, db: Session = Depends(get_db)):
    src = _get_article(db, article_id)
    copy = Article(
        title=f"{src.title} (Kopie)",
        description=src.description,
        category=src.category,
        condition=src.condition,
        status="Entwurf",  # Kopie startet als Entwurf
        quantity=1,
        purchase_cost=src.purchase_cost,
        listing_price=src.listing_price,
        shipping_method=src.shipping_method,
        shipping_cost=src.shipping_cost,
        shipping_payer=src.shipping_payer,
        tags=src.tags,
        note=src.note,
        # Verkaufshistorie und Links bewusst NICHT übernehmen
    )
    db.add(copy)
    assign_article_no(db, copy)
    db.commit()
    return RedirectResponse(f"/articles/{copy.id}/edit", status_code=303)


@router.post("/articles/{article_id}/delete")
def delete_article(article_id: int, db: Session = Depends(get_db)):
    article = _get_article(db, article_id)
    # zugehörige Bilddateien (inkl. Thumbnails) entfernen
    for img in article.images:
        _delete_image_files(img.filename)
    db.delete(article)
    db.commit()
    return RedirectResponse("/articles", status_code=303)


# ---------------------------------------------------------------------------
# Bilder
# ---------------------------------------------------------------------------
@router.post("/articles/{article_id}/images")
async def upload_image(
    article_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    article = _get_article(db, article_id)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXT:
        raise HTTPException(status_code=400, detail="Ungültiges Bildformat")

    try:
        # verkleinert speichern + Thumbnail erzeugen
        filename = images.process_upload(
            await file.read(), config.UPLOAD_DIR, uuid.uuid4().hex
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Bild konnte nicht gelesen werden")

    # neues Bild ans Ende sortieren
    next_pos = max((img.position for img in article.images), default=-1) + 1
    db.add(ArticleImage(article_id=article.id, filename=filename, position=next_pos))
    db.commit()
    return RedirectResponse(f"/articles/{article_id}", status_code=303)


@router.post("/articles/{article_id}/images/{image_id}/main")
def set_main_image(article_id: int, image_id: int, db: Session = Depends(get_db)):
    article = _get_article(db, article_id)
    # ausgewähltes Bild nach vorne, Rest in bisheriger Reihenfolge dahinter
    ordered = sorted(article.images, key=lambda i: (i.position, i.id))
    ordered = [i for i in ordered if i.id == image_id] + [i for i in ordered if i.id != image_id]
    for pos, img in enumerate(ordered):
        img.position = pos
    db.commit()
    return RedirectResponse(f"/articles/{article_id}", status_code=303)


@router.post("/articles/{article_id}/images/{image_id}/delete")
def delete_image(article_id: int, image_id: int, db: Session = Depends(get_db)):
    img = db.get(ArticleImage, image_id)
    if img and img.article_id == article_id:
        _delete_image_files(img.filename)
        db.delete(img)
        db.commit()
    return RedirectResponse(f"/articles/{article_id}", status_code=303)


# ---------------------------------------------------------------------------
# CSV-Export
