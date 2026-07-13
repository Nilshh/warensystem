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

# Marktplatz und Umgebung (sandbox|production)
EBAY_MARKETPLACE_ID = os.getenv("EBAY_MARKETPLACE_ID", "EBAY_DE")
EBAY_ENV = os.getenv("EBAY_ENV", "production").lower()

# API-Basis-URLs je Umgebung
_EBAY_HOSTS = {
    "production": "https://api.ebay.com",
    "sandbox": "https://api.sandbox.ebay.com",
}
EBAY_API_BASE = _EBAY_HOSTS.get(EBAY_ENV, _EBAY_HOSTS["production"])


def ebay_import_configured() -> bool:
    """True, sobald App-Keys für den Import (Browse API) vorhanden sind.

    Der Import nutzt nur einen App-Token (Client-Credentials) — also reichen
    Client-ID und Client-Secret, kein Nutzer-Refresh-Token.
    """
    return bool(EBAY_CLIENT_ID and EBAY_CLIENT_SECRET)


def ebay_configured() -> bool:
    """True, sobald ALLE eBay-Zugangsdaten (inkl. Refresh-Token) hinterlegt sind.

    Wird für die vollautomatische Verkaufs-Synchronisierung benötigt.
    """
    return bool(EBAY_CLIENT_ID and EBAY_CLIENT_SECRET and EBAY_REFRESH_TOKEN)
