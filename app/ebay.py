"""eBay-Anbindung.

Zwei Ebenen:

1. **Import per Link (Browse API)** — aktiv, sobald Client-ID + Client-Secret in
   der .env stehen. Nutzt einen App-Token (Client-Credentials, kein Nutzer-Login)
   und liest öffentlich sichtbare Inseratsdaten (Titel, Preis, Zustand,
   Beschreibung, Bilder). Siehe ``fetch_item()``.

2. **Verkaufs-Sync (Sell API)** — Platzhalter, benötigt zusätzlich einen
   Nutzer-Refresh-Token. Siehe ``sync_orders()``.

Bewusst nur mit der Standardbibliothek (urllib), um keine weitere Abhängigkeit
einzuführen.
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

# einfacher Prozess-lokaler Token-Cache: (token, ablauf_epoch)
_token_cache: tuple[str, float] | None = None


class EbayError(RuntimeError):
    """Fehler bei der eBay-Kommunikation (benutzerfreundliche Meldung)."""


# ---------------------------------------------------------------------------
# Verfügbarkeit
# ---------------------------------------------------------------------------
def import_supported() -> bool:
    return config.ebay_import_configured()


def is_configured() -> bool:
    return config.ebay_configured()


# ---------------------------------------------------------------------------
# OAuth (Client-Credentials / App-Token)
# ---------------------------------------------------------------------------
def _get_app_token() -> str:
    global _token_cache
    if _token_cache and _token_cache[1] > time.time() + 60:
        return _token_cache[0]

    creds = f"{config.EBAY_CLIENT_ID}:{config.EBAY_CLIENT_SECRET}".encode()
    auth = base64.b64encode(creds).decode()
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope",
    }).encode()
    req = urllib.request.Request(
        f"{config.EBAY_API_BASE}/identity/v1/oauth2/token",
        data=body,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise EbayError(f"eBay-Anmeldung fehlgeschlagen ({e.code}). Prüfe Client-ID/Secret. {detail}")
    except urllib.error.URLError as e:
        raise EbayError(f"eBay nicht erreichbar: {e.reason}")

    token = data.get("access_token")
    if not token:
        raise EbayError("eBay lieferte keinen Access-Token.")
    _token_cache = (token, time.time() + int(data.get("expires_in", 7200)))
    return token


# ---------------------------------------------------------------------------
# Item-ID aus einer eBay-URL extrahieren
# ---------------------------------------------------------------------------
def extract_item_id(url_or_id: str) -> str | None:
    """Liefert die numerische Legacy-Item-ID aus URL oder direkter Eingabe."""
    s = (url_or_id or "").strip()
    if not s:
        return None
    if s.isdigit():
        return s
    # /itm/<titel>/1234567890  oder  /itm/1234567890
    m = re.search(r"/itm/(?:[^/]+/)?(\d{9,15})", s)
    if m:
        return m.group(1)
    # ?item=... oder &itemId=...
    try:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(s).query)
        for key in ("item", "itemId", "itm"):
            if key in qs and qs[key][0].isdigit():
                return qs[key][0]
    except ValueError:
        pass
    # Fallback: längste passende Ziffernfolge
    nums = re.findall(r"\d{9,15}", s)
    return nums[0] if nums else None


# ---------------------------------------------------------------------------
# Item über die Browse API laden
# ---------------------------------------------------------------------------
def fetch_item(url_or_id: str) -> dict:
    """Lädt ein eBay-Inserat und gibt ein normalisiertes Dict zurück.

    Keys: title, price, currency, condition, description, item_web_url,
          ebay_item_id, image_urls (list), quantity.
    """
    if not import_supported():
        raise EbayError(
            "eBay-Import ist nicht konfiguriert. Trage EBAY_CLIENT_ID und "
            "EBAY_CLIENT_SECRET in die .env ein."
        )
    item_id = extract_item_id(url_or_id)
    if not item_id:
        raise EbayError("Keine gültige eBay-Artikelnummer im Link gefunden.")

    token = _get_app_token()
    endpoint = (
        f"{config.EBAY_API_BASE}/buy/browse/v1/item/get_item_by_legacy_id"
        f"?legacy_item_id={item_id}"
    )
    req = urllib.request.Request(
        endpoint,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": config.EBAY_MARKETPLACE_ID,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            item = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise EbayError(f"Kein Inserat mit der Artikelnummer {item_id} gefunden.")
        detail = e.read().decode(errors="replace")[:300]
        raise EbayError(f"eBay-Abruf fehlgeschlagen ({e.code}). {detail}")
    except urllib.error.URLError as e:
        raise EbayError(f"eBay nicht erreichbar: {e.reason}")

    # Verfügbare Stückzahl aus dem Inserat (Standard 1)
    quantity = 1
    avails = item.get("estimatedAvailabilities") or []
    if avails:
        qty = avails[0].get("estimatedAvailableQuantity")
        if isinstance(qty, int) and qty > 0:
            quantity = qty

    price = item.get("price") or {}
    images = []
    if item.get("image", {}).get("imageUrl"):
        images.append(item["image"]["imageUrl"])
    for extra in item.get("additionalImages", []) or []:
        if extra.get("imageUrl"):
            images.append(extra["imageUrl"])

    # Beschreibung: HTML grob zu Text vereinfachen
    raw_desc = item.get("description") or item.get("shortDescription") or ""
    description = _html_to_text(raw_desc)

    return {
        "title": item.get("title", "").strip(),
        "price": _to_float(price.get("value")),
        "currency": price.get("currency", ""),
        "condition": item.get("condition", "").strip(),
        "description": description,
        "item_web_url": item.get("itemWebUrl", ""),
        "ebay_item_id": item_id,
        "image_urls": images[:12],
        "quantity": quantity,
    }


def download_image(image_url: str, dest_path) -> bool:
    """Lädt ein Bild herunter. Gibt True bei Erfolg zurück."""
    try:
        req = urllib.request.Request(image_url, headers={"User-Agent": "warensystem/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        with open(dest_path, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Verkaufs-Sync (Platzhalter, benötigt Refresh-Token)
# ---------------------------------------------------------------------------
def sync_orders(db) -> int:
    if not is_configured():
        raise EbayError("Verkaufs-Sync benötigt zusätzlich einen eBay-Refresh-Token.")
    raise NotImplementedError(
        "eBay-Sync ist vorbereitet, aber noch nicht implementiert. Siehe app/ebay.py."
    )


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _html_to_text(html: str) -> str:
    """Sehr einfache HTML->Text-Reduktion für die Beschreibung."""
    if not html:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    # HTML-Entities minimal auflösen
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
        text = text.replace(a, b)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()
