"""eBay-API-Anbindung — vorbereiteter Platzhalter.

Das Datenmodell (`Article.ebay_item_id`, `ebay_url`) und die UI sind bereits
auf eine spätere Synchronisierung ausgelegt. Sobald ein eBay-Developer-Account
vorhanden ist:

1. Zugangsdaten als Umgebungsvariablen setzen
   (EBAY_CLIENT_ID / EBAY_CLIENT_SECRET / EBAY_REFRESH_TOKEN in docker-compose.yml).
2. `sync_orders()` implementieren — z.B. über die eBay Sell APIs
   (Fulfillment API: GET /sell/fulfillment/v1/order) via OAuth-Bearer-Token.
3. Verkaufte Artikel anhand `ebay_item_id` matchen und Status/Preise aktualisieren.

Kleinanzeigen bietet keine offizielle API und bleibt daher manuell.
"""
from . import config


def is_configured() -> bool:
    return config.ebay_configured()


def sync_orders(db) -> int:
    """Platzhalter. Gibt die Anzahl aktualisierter Artikel zurück.

    Wirft (noch) NotImplementedError, bis die API-Logik ergänzt wurde.
    """
    if not is_configured():
        raise RuntimeError(
            "eBay-API ist nicht konfiguriert. Bitte Zugangsdaten in "
            "docker-compose.yml hinterlegen."
        )
    raise NotImplementedError(
        "eBay-Sync ist vorbereitet, aber noch nicht implementiert. "
        "Siehe app/ebay.py für die nächsten Schritte."
    )
