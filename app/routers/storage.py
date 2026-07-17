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


@router.get("/storage/location", response_class=HTMLResponse)
def storage_location(
    request: Request, area: str = "", shelf: str = "", bin: str = "",
    db: Session = Depends(get_db),
):
    """Inhalt eines bestimmten Lagerorts (Ziel der Lager-QR-Codes)."""
    articles = db.scalars(
        select(Article)
        .options(selectinload(Article.images))   # Vorschaubilder je Zeile
        .where(
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
