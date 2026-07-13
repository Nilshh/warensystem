"""Zentrale Konfiguration (per Umgebungsvariablen steuerbar)."""
import os
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "warensystem.db"

# Verzeichnisse sicherstellen
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Standard-Gebührensatz von eBay (nur Vorschlag im Formular)
DEFAULT_EBAY_FEE_PERCENT = float(os.getenv("EBAY_FEE_PERCENT", "11.0"))

# eBay-API — für die spätere Anbindung vorbereitet
EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "")
EBAY_REFRESH_TOKEN = os.getenv("EBAY_REFRESH_TOKEN", "")


def ebay_configured() -> bool:
    """True, sobald alle eBay-Zugangsdaten hinterlegt sind."""
    return bool(EBAY_CLIENT_ID and EBAY_CLIENT_SECRET and EBAY_REFRESH_TOKEN)
