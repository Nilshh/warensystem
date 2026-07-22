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

# Verkaufte Artikel nach X Tagen automatisch archivieren (0 = deaktiviert)
ARCHIVE_AFTER_DAYS = int(os.getenv("ARCHIVE_AFTER_DAYS", "7"))

# Zugestellte Verkäufe nach X Tagen automatisch abschließen (0 = deaktiviert)
AUTO_COMPLETE_DAYS = int(os.getenv("AUTO_COMPLETE_DAYS", "14"))

# Automatische Sicherungen: Intervall in Stunden (0 = deaktiviert)
AUTO_BACKUP_HOURS = int(os.getenv("AUTO_BACKUP_HOURS", "24"))
# Ablageort der Sicherungen (im Container per Volume auf ./backups gemappt)
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "./backups"))
# Wie viele automatische Sicherungen aufgehoben werden (0 = alle behalten)
KEEP_BACKUPS = int(os.getenv("KEEP_BACKUPS", "10"))

# Präfix der automatischen Artikelnummer (z.B. WA-00001)
ARTICLE_NO_PREFIX = os.getenv("ARTICLE_NO_PREFIX", "WA-")

# Basis-URL für QR-Codes/Etiketten (ohne abschließenden Slash)
BASE_URL = os.getenv("BASE_URL", "http://wa.home").rstrip("/")

# Absenderdaten für Lieferschein/Packzettel
SELLER_NAME = os.getenv("SELLER_NAME", "")
SELLER_ADDRESS = os.getenv("SELLER_ADDRESS", "")   # mehrzeilig via \n
SELLER_EMAIL = os.getenv("SELLER_EMAIL", "")
SELLER_PHONE = os.getenv("SELLER_PHONE", "")

# eBay-API — für die spätere Anbindung vorbereitet
EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "")
EBAY_REFRESH_TOKEN = os.getenv("EBAY_REFRESH_TOKEN", "")

# --- Sendungsverfolgung -----------------------------------------------------
# DHL-Tracking (kostenloser API-Key von developer.dhl.com). Ohne Key inaktiv.
DHL_API_KEY = os.getenv("DHL_API_KEY", "")
# Abstand zwischen den Abfragen in Stunden (12 = 2x täglich, 0 = aus)
TRACKING_INTERVAL_HOURS = int(os.getenv("TRACKING_INTERVAL_HOURS", "12"))
# Sendungen, die länger als X Tage unterwegs sind, im Dashboard melden
TRACKING_STUCK_DAYS = int(os.getenv("TRACKING_STUCK_DAYS", "7"))
# Nach X Tagen ohne Zustellung nicht weiter abfragen (schont das Kontingent)
TRACKING_MAX_DAYS = int(os.getenv("TRACKING_MAX_DAYS", "60"))


def tracking_configured() -> bool:
    return bool(DHL_API_KEY)


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
