"""Templates und Anzeige-Formatierung (deutsche Schreibweise)."""
from pathlib import Path

from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


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
