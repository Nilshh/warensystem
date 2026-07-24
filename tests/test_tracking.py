"""Sendungsverfolgung: DHL-Anbindung und Abfrage-Logik."""
import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from app import carriers, config, maintenance
from app.models import Article, Sale


def _dhl_antwort(status_code, text="Zustellung erfolgreich", ts="2026-07-15T10:00:00Z"):
    return json.dumps({"shipments": [{"status": {
        "statusCode": status_code, "status": text, "timestamp": ts}}]}).encode()


@pytest.fixture
def dhl(monkeypatch):
    """Simuliert die DHL-API; gibt eine Steuerung für die Antwort zurück."""
    monkeypatch.setattr(config, "DHL_API_KEY", "test-key")
    steuerung = {"body": _dhl_antwort("transit", "In Zustellung"), "fehler": None,
                 "requests": []}

    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        steuerung["requests"].append(req)
        if steuerung["fehler"]:
            raise steuerung["fehler"]
        return _Resp(steuerung["body"])

    monkeypatch.setattr(carriers.urllib.request, "urlopen", fake_urlopen)
    return steuerung


# --- Anbieter-Erkennung -----------------------------------------------------
@pytest.mark.parametrize("hinweis,erwartet", [
    ("DHL", "dhl"),
    ("DHL Paket 2 kg (60×30×15, mit Tracking)", "dhl"),
    ("dhl päckchen", "dhl"),
    ("Hermes", None),          # keine offizielle API -> bleibt manuell
    ("UPS", None),
    ("", None),
])
def test_anbieter_erkennung(hinweis, erwartet):
    assert carriers.detect(hinweis) == erwartet


def test_erkennung_nutzt_mehrere_hinweise():
    # Dienstleister leer, aber Versandart verrät ihn
    assert carriers.detect("", "DHL Päckchen S") == "dhl"


# --- DHL-Statusabbildung ----------------------------------------------------
@pytest.mark.parametrize("code,erwartet", [
    ("delivered", carriers.ZUGESTELLT),
    ("transit", carriers.UNTERWEGS),
    ("pre-transit", carriers.UNTERWEGS),
    ("failure", carriers.PROBLEM),
    ("unknown", carriers.UNBEKANNT),
    ("etwas-neues", carriers.UNBEKANNT),      # unbekannte Codes brechen nichts
])
def test_dhl_status_abbildung(dhl, code, erwartet):
    dhl["body"] = _dhl_antwort(code)
    assert carriers.track("dhl", "00340001").status == erwartet


def test_dhl_zustellzeitpunkt_wird_gelesen(dhl):
    dhl["body"] = _dhl_antwort("delivered", ts="2026-07-15T10:00:00Z")
    res = carriers.track("dhl", "00340001")
    assert res.delivered_at == datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
    assert res.text == "Zustellung erfolgreich"


def test_dhl_unbekannte_nummer_ist_kein_fehler(dhl):
    dhl["fehler"] = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    assert carriers.track("dhl", "00340001").status == carriers.UNBEKANNT


@pytest.mark.parametrize("code", [429, 500])
def test_dhl_fehler_werden_gemeldet(dhl, code):
    dhl["fehler"] = urllib.error.HTTPError("u", code, "Fehler", {}, None)
    with pytest.raises(carriers.TrackingError):
        carriers.track("dhl", "00340001")


@pytest.mark.parametrize("code", [401, 403])
def test_abgelehnter_zugriff_nennt_beide_ursachen(dhl, code):
    """DHL antwortet bei falschem Key und bei fehlender Freigabe gleich —
    die Meldung darf sich also nicht auf eine Ursache festlegen."""
    dhl["fehler"] = urllib.error.HTTPError("u", code, "Unauthorized", {}, None)
    with pytest.raises(carriers.TrackingError) as e:
        carriers.track("dhl", "00340001")
    assert "Consumer Secret" in str(e.value)      # Ursache 1: falscher Key
    assert "freigegeben" in str(e.value)          # Ursache 2: App nicht freigegeben


def test_ohne_api_key_kein_zugriff(monkeypatch):
    monkeypatch.setattr(config, "DHL_API_KEY", "")
    assert carriers.is_configured() is False
    with pytest.raises(carriers.TrackingError):
        carriers.track("dhl", "00340001")


def test_api_key_wird_mitgeschickt(dhl):
    carriers.track("dhl", "00340001")
    req = dhl["requests"][0]
    assert req.get_header("Dhl-api-key") == "test-key"
    assert "trackingNumber=00340001" in req.full_url


# --- Abfrage-Logik ----------------------------------------------------------
def _verkauf(db, **kw):
    a = Article(title="Ware", status="Angeboten", quantity=5)
    db.add(a)
    db.commit()
    s = Sale(article_id=a.id, quantity=1, sold_price=10, shipping_payer="Käufer",
             sold_at=kw.pop("sold_at", datetime.now(timezone.utc)), **kw)
    db.add(s)
    db.commit()
    return s


def test_zugestellte_sendung_wird_gespeichert(dhl, db):
    s = _verkauf(db, tracking_carrier="DHL", tracking_number="00340001")
    dhl["body"] = _dhl_antwort("delivered")

    assert maintenance.update_tracking().updated == 1
    db.refresh(s)
    assert s.tracking_status == carriers.ZUGESTELLT
    assert s.is_delivered is True
    assert s.tracking_delivered_at is not None
    assert s.tracking_checked_at is not None


def test_zugestellte_sendung_wird_nicht_erneut_abgefragt(dhl, db):
    s = _verkauf(db, tracking_carrier="DHL", tracking_number="00340001",
                 tracking_status=carriers.ZUGESTELLT)
    assert maintenance.update_tracking().updated == 0
    assert dhl["requests"] == []          # gar keine Abfrage


def test_ohne_sendungsnummer_keine_abfrage(dhl, db):
    _verkauf(db, tracking_carrier="DHL", tracking_number="")
    assert maintenance.update_tracking().updated == 0
    assert dhl["requests"] == []


def test_hermes_wird_uebersprungen(dhl, db):
    _verkauf(db, tracking_carrier="Hermes", tracking_number="H123")
    assert maintenance.update_tracking().updated == 0
    assert dhl["requests"] == []          # kein Anbieter -> keine Abfrage


def test_sehr_alte_sendung_wird_nicht_mehr_abgefragt(dhl, db, monkeypatch):
    monkeypatch.setattr(config, "TRACKING_MAX_DAYS", 60)
    _verkauf(db, tracking_carrier="DHL", tracking_number="00340001",
             sold_at=datetime.now(timezone.utc) - timedelta(days=90))
    assert maintenance.update_tracking().updated == 0
    assert dhl["requests"] == []


def test_fehler_bei_einer_sendung_stoppt_die_anderen_nicht(dhl, db, monkeypatch):
    _verkauf(db, tracking_carrier="DHL", tracking_number="A1")
    _verkauf(db, tracking_carrier="DHL", tracking_number="A2")

    aufrufe = {"n": 0}
    original = carriers.track

    def flaky(carrier, number):
        aufrufe["n"] += 1
        if number == "A1":
            raise carriers.TrackingError("Netzwerkfehler")
        return original(carrier, number)

    monkeypatch.setattr(carriers, "track", flaky)
    assert maintenance.update_tracking().updated == 1      # A2 wurde trotzdem aktualisiert
    assert aufrufe["n"] == 2


def test_ohne_key_laeuft_die_abfrage_gar_nicht(db, monkeypatch):
    monkeypatch.setattr(config, "DHL_API_KEY", "")
    _verkauf(db, tracking_carrier="DHL", tracking_number="00340001")
    assert maintenance.update_tracking().updated == 0


# --- Dashboard-Hinweis ------------------------------------------------------
def test_dashboard_meldet_haengende_sendung(dhl, db, client, monkeypatch):
    monkeypatch.setattr(config, "TRACKING_STUCK_DAYS", 7)
    _verkauf(db, tracking_carrier="DHL", tracking_number="00340001",
             tracking_status=carriers.UNTERWEGS,
             sold_at=datetime.now(timezone.utc) - timedelta(days=10))
    html = client.get("/").text
    assert "Sendungen unterwegs seit über 7 Tagen" in html
    assert "00340001" in html


def test_dashboard_meldet_frische_sendung_nicht(dhl, db, client):
    _verkauf(db, tracking_carrier="DHL", tracking_number="00340001",
             tracking_status=carriers.UNTERWEGS,
             sold_at=datetime.now(timezone.utc) - timedelta(days=1))
    assert "Sendungen unterwegs seit über" not in client.get("/").text


# --- Manueller Anstoß -------------------------------------------------------
def test_manueller_lauf_aktualisiert(dhl, db, client):
    s = _verkauf(db, tracking_carrier="DHL", tracking_number="00340001")
    dhl["body"] = _dhl_antwort("delivered")

    r = client.post("/tracking/refresh", data={"back": "/"}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    db.refresh(s)
    assert s.tracking_status == carriers.ZUGESTELLT


def test_manueller_lauf_meldet_fehler_in_der_oberflaeche(dhl, db, client):
    """Bei einem API-Fehler soll die Meldung sichtbar sein, nicht nur im Log."""
    _verkauf(db, tracking_carrier="DHL", tracking_number="00340001")
    dhl["fehler"] = urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    r = client.post("/tracking/refresh", data={"back": "/"}, follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_manueller_lauf_ohne_offene_sendungen(dhl, db, client):
    r = client.post("/tracking/refresh", data={"back": "/"}, follow_redirects=False)
    assert "Keine%20offenen" in r.headers["location"]


def test_einzelne_sendung_manuell_pruefen(dhl, db, client):
    s = _verkauf(db, shipping_method="DHL Paket 2 kg", tracking_number="00340001")
    dhl["body"] = _dhl_antwort("delivered")

    r = client.post(f"/sales/{s.id}/tracking-refresh", data={}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    db.refresh(s)
    assert s.tracking_status == carriers.ZUGESTELLT


def test_einzelpruefung_meldet_nicht_unterstuetzten_anbieter(dhl, db, client):
    s = _verkauf(db, shipping_method="Hermes", tracking_number="H123")
    r = client.post(f"/sales/{s.id}/tracking-refresh", data={}, follow_redirects=False)
    assert "nicht" in r.headers["location"]      # Hinweis statt stiller Wirkungslosigkeit
    assert dhl["requests"] == []


def test_stornierte_sendung_wird_nicht_abgefragt(dhl, db):
    from app.models import FULFILLMENT_CANCELLED
    _verkauf(db, tracking_carrier="DHL", tracking_number="00340001",
             fulfillment=FULFILLMENT_CANCELLED)
    assert maintenance.update_tracking().checked == 0
    assert dhl["requests"] == []


def test_versandart_allein_reicht_als_anbieter(dhl, db):
    """Ohne Dienstleister-Feld: die Versandart verrät den Anbieter."""
    s = _verkauf(db, shipping_method="DHL Päckchen S (2 kg, 35×25×10)",
                 tracking_number="00340001")
    dhl["body"] = _dhl_antwort("transit", "In Zustellung")
    assert maintenance.update_tracking().updated == 1
    db.refresh(s)
    assert s.tracking_status == carriers.UNTERWEGS
    assert s.carrier_label.startswith("DHL")


def test_sendungsdaten_bleiben_sichtbar(db, client):
    """Trackingnummer und DHL-Status müssen in Liste und Historie stehen."""
    s = _verkauf(db, tracking_carrier="DHL", tracking_number="00340434292135100186",
                 shipping_method="DHL Paket 2 kg", tracking_status="unterwegs",
                 tracking_status_text="In Zustellung")
    for seite in ("/sales", f"/articles/{s.article_id}"):
        html = client.get(seite).text
        assert "00340434292135100186" in html, f"Trackingnummer fehlt in {seite}"
        assert "Unterwegs" in html, f"DHL-Status fehlt in {seite}"
    # Versandart gehört in die Artikel-Historie
    assert "DHL Paket 2 kg" in client.get(f"/articles/{s.article_id}").text


def test_dashboard_meldet_zugestellte_nicht(dhl, db, client):
    _verkauf(db, tracking_carrier="DHL", tracking_number="00340001",
             tracking_status=carriers.ZUGESTELLT,
             sold_at=datetime.now(timezone.utc) - timedelta(days=30))
    assert "Sendungen unterwegs seit über" not in client.get("/").text
