"""Routen: storage."""
import urllib.parse

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse, Response
from markupsafe import Markup
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import images
from ..database import get_db
from ..models import Article, StorageLocation
from ..services import all_locations, make_qr_svg, format_storage, _storage_query, _storage_url
from ..web import templates

router = APIRouter()


@router.get("/storage", response_class=HTMLResponse)
def storage_overview(request: Request, error: str = "", db: Session = Depends(get_db)):
    """Übersicht der verwalteten Lagerorte mit Anzahl; Lagerorte hier anlegen.

    Der Inhalt eines Lagerorts wird erst auf dessen Detailseite gezeigt —
    hier wird deshalb nur gezählt, nicht geladen.
    """
    locations = []
    for loc in all_locations(db):
        count = db.scalar(
            select(func.count(Article.id)).where(
                Article.storage_area == loc.area,
                Article.storage_shelf == loc.shelf,
                Article.storage_bin == loc.bin,
            )
        ) or 0
        locations.append({
            "id": loc.id, "area": loc.area, "shelf": loc.shelf, "bin": loc.bin,
            "label": loc.label, "count": count,
        })
    return templates.TemplateResponse(
        "storage_overview.html", {"request": request, "locations": locations, "error": error}
    )


@router.post("/storage/new")
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


@router.post("/storage/{loc_id}/edit")
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


@router.post("/storage/{loc_id}/delete")
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


def _location_articles(db: Session, area: str, shelf: str, bin: str):
    return db.scalars(
        select(Article)
        .options(selectinload(Article.images))   # Vorschaubilder je Zeile
        .where(
            Article.storage_area == area,
            Article.storage_shelf == shelf,
            Article.storage_bin == bin,
        ).order_by(Article.article_no)
    ).all()


@router.get("/storage/location", response_class=HTMLResponse)
def storage_location(
    request: Request, area: str = "", shelf: str = "", bin: str = "",
    msg: str = "", error: str = "",
    db: Session = Depends(get_db),
):
    """Inhalt eines bestimmten Lagerorts (Ziel der Lager-QR-Codes)."""
    articles = _location_articles(db, area, shelf, bin)
    label = format_storage(area, shelf, bin)
    return templates.TemplateResponse(
        "storage_location.html",
        {
            "request": request, "articles": articles, "label": label,
            "area": area, "shelf": shelf, "bin": bin,
            "msg": msg, "error": error,
            "query": _storage_query(area, shelf, bin),
        },
    )


@router.post("/storage/location/assign")
async def storage_location_assign(
    request: Request,
    area: str = Form(""), shelf: str = Form(""), bin: str = Form(""),
    article_no: str = Form(""),
    db: Session = Depends(get_db),
):
    """Lagert einen Artikel per Artikelnummer auf diesen Lagerplatz ein.

    Direkt am Regal nutzbar: Lager-QR scannen, Nummer eintippen, fertig —
    auch zum Umlagern von einem anderen Platz. Mit htmx wird nur das
    Listen-Fragment neu gerendert, ohne JavaScript die ganze Seite.
    """
    from ..services import make_article_no

    needle = article_no.strip()
    article = None
    if needle:
        article = db.scalar(
            select(Article).where(func.lower(Article.article_no) == needle.lower())
        )
        if not article and needle.isdigit():
            # Bequemlichkeit: "123" wird zu "WA-00123" (bzw. konfigurierter Präfix)
            article = db.scalar(
                select(Article).where(Article.article_no == make_article_no(int(needle)))
            )

    msg = error = ""
    if not needle:
        error = "Bitte eine Artikelnummer angeben."
    elif not article:
        error = f"Kein Artikel mit der Nummer „{needle}“ gefunden."
    else:
        vorher = article.storage_location
        article.storage_area, article.storage_shelf, article.storage_bin = area, shelf, bin
        db.commit()
        msg = f"{article.article_no} {article.title} eingelagert."
        if vorher and vorher != format_storage(area, shelf, bin):
            msg = f"{article.article_no} {article.title} umgelagert (vorher: {vorher})."

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "partials/storage_articles.html",
            {
                "request": request,
                "articles": _location_articles(db, area, shelf, bin),
                "area": area, "shelf": shelf, "bin": bin,
                "msg": msg, "error": error,
            },
        )
    q = _storage_query(area, shelf, bin)
    tail = f"&msg={urllib.parse.quote(msg)}" if msg else f"&error={urllib.parse.quote(error)}"
    return RedirectResponse(f"/storage/location?{q}{tail}", status_code=303)


@router.get("/storage/qr.svg")
def storage_qr(area: str = "", shelf: str = "", bin: str = ""):
    svg = make_qr_svg(_storage_url(area, shelf, bin))
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/storage/label", response_class=HTMLResponse)
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
