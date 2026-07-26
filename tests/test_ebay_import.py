"""eBay-Import: robustes Parsen und Fehlerbehandlung (nie 500)."""
import io
import json
import urllib.error

import pytest

from app import ebay
from app.models import Article


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.fixture
def ebay_ok(monkeypatch):
    """eBay konfiguriert + gültiges App-Token; Item-Antwort steuerbar."""
    monkeypatch.setattr(ebay.config, "EBAY_CLIENT_ID", "id")
    monkeypatch.setattr(ebay.config, "EBAY_CLIENT_SECRET", "secret")
    monkeypatch.setattr(ebay, "_get_app_token", lambda: "tok")
    box = {"body": b"{}", "error": None}

    def fake_urlopen(req, timeout=None):
        if box["error"]:
            raise box["error"]
        return _Resp(box["body"])

    monkeypatch.setattr(ebay.urllib.request, "urlopen", fake_urlopen)
    return box


def _item_json(**over):
    base = {
        "title": "PIKO 57743 Containerwagen",
        "price": {"value": "14.00", "currency": "EUR"},
        "condition": "Neu",
        "image": {"imageUrl": "https://img/x.jpg"},
        "itemWebUrl": "https://ebay.de/itm/1234567890",
    }
    base.update(over)
    return json.dumps(base).encode()


# --- Robustes Parsen --------------------------------------------------------
def test_normale_antwort(ebay_ok):
    ebay_ok["body"] = _item_json()
    item = ebay.fetch_item("https://www.ebay.de/itm/1234567890")
    assert item["title"].startswith("PIKO")
    assert item["price"] == 14.0
    assert item["image_urls"] == ["https://img/x.jpg"]
    assert item["quantity"] == 1


def test_null_felder_brechen_nicht(ebay_ok):
    """eBay liefert Felder auch als null — darf keinen 500 geben."""
    ebay_ok["body"] = _item_json(image=None, title=None, condition=None,
                                 itemWebUrl=None, price=None)
    item = ebay.fetch_item("https://www.ebay.de/itm/1234567890")
    assert item["title"] == ""
    assert item["condition"] == ""
    assert item["image_urls"] == []
    assert item["price"] == 0.0


def test_zusatzbilder_mit_null_eintrag(ebay_ok):
    ebay_ok["body"] = _item_json(additionalImages=[{"imageUrl": "https://img/a.jpg"},
                                                   None,
                                                   {"imageUrl": None}])
    item = ebay.fetch_item("https://www.ebay.de/itm/1234567890")
    assert item["image_urls"] == ["https://img/x.jpg", "https://img/a.jpg"]


def test_stueckzahl_aus_inserat(ebay_ok):
    ebay_ok["body"] = _item_json(
        estimatedAvailabilities=[{"estimatedAvailableQuantity": 7}])
    assert ebay.fetch_item("https://www.ebay.de/itm/1234567890")["quantity"] == 7


# --- Netzwerk-/Antwortfehler werden zu EbayError (kein 500) -----------------
def test_kein_json_gibt_ebayerror(ebay_ok):
    ebay_ok["body"] = b"<html>irgendein Fehler</html>"
    with pytest.raises(ebay.EbayError):
        ebay.fetch_item("https://www.ebay.de/itm/1234567890")


def test_timeout_gibt_ebayerror(ebay_ok):
    ebay_ok["error"] = TimeoutError("timed out")
    with pytest.raises(ebay.EbayError):
        ebay.fetch_item("https://www.ebay.de/itm/1234567890")


def test_404_gibt_freundliche_meldung(ebay_ok):
    ebay_ok["error"] = urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b""))
    with pytest.raises(ebay.EbayError, match="Kein Inserat"):
        ebay.fetch_item("https://www.ebay.de/itm/1234567890")


# --- Import-Route: nie 500 --------------------------------------------------
def test_import_route_zeigt_meldung_statt_500(client, db, monkeypatch):
    """Selbst eine unerwartete Ausnahme wird zur Fehlermeldung, kein 500."""
    monkeypatch.setattr(ebay, "import_supported", lambda: True)

    def boom(url):
        raise RuntimeError("unerwartet")
    monkeypatch.setattr(ebay, "fetch_item", boom)

    r = client.post("/articles/import-ebay",
                    data={"ebay_url": "https://www.ebay.de/itm/1"}, follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    assert db.query(Article).count() == 0


def test_import_route_erfolg(client, db, monkeypatch):
    monkeypatch.setattr(ebay, "import_supported", lambda: True)
    monkeypatch.setattr(ebay, "fetch_item", lambda url: {
        "title": "Importiert", "price": 9.99, "currency": "EUR", "condition": "Neu",
        "description": "x", "item_web_url": "https://ebay.de/itm/1",
        "ebay_item_id": "1", "image_urls": [], "quantity": 3,
    })
    r = client.post("/articles/import-ebay",
                    data={"ebay_url": "https://www.ebay.de/itm/1"}, follow_redirects=False)
    assert r.status_code == 303
    a = db.query(Article).filter_by(title="Importiert").one()
    assert a.quantity == 3 and a.status == "Entwurf"
