"""Templates und Anzeige-Formatierung (deutsche Schreibweise)."""
from pathlib import Path

from fastapi.templating import Jinja2Templates

from .models import FULFILLMENT_STEPS, FULFILLMENT_CANCELLED, fulfillment_rank

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Abwicklungsstatus: Beschriftung (mit Symbol) und CSS-Klasse
FULFILLMENT_LABELS = {
    "Verkauft": "🛒 Verkauft",
    "Bezahlt": "💶 Bezahlt",
    "Versendet": "📦 Versendet",
    "Zugestellt": "✓ Zugestellt",
    "Abgeschlossen": "✔ Abgeschlossen",
    FULFILLMENT_CANCELLED: "✕ Storniert",
}

templates.env.globals["fulfillment_label"] = lambda s: FULFILLMENT_LABELS.get(s, s or "–")
templates.env.globals["fulfillment_rank"] = fulfillment_rank
templates.env.globals["FULFILLMENT_STEPS"] = FULFILLMENT_STEPS
templates.env.globals["FULFILLMENT_CANCELLED"] = FULFILLMENT_CANCELLED


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
