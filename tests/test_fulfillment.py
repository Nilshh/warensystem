"""Abwicklungs-Ablauf eines Verkaufs: Status, Automatik, Storno."""
from datetime import datetime, timedelta, timezone

from app import carriers, config, maintenance, services
from app.models import Article, Sale, FULFILLMENT_CANCELLED


def _artikel_mit_verkauf(db, qty=5, verkauft=1, **sale_kw):
    a = Article(title="Ware", status="Angeboten", quantity=qty, purchase_cost=2)
    db.add(a)
    db.commit()
    a.quantity -= verkauft
    services._sync_stock_status(a)
    s = Sale(article_id=a.id, quantity=verkauft, sold_price=20, unit_purchase_cost=2,
             shipping_payer="Käufer", sold_at=datetime.now(timezone.utc), **sale_kw)
    db.add(s)
    db.commit()
    return a, s


# --- Grundzustand -----------------------------------------------------------
def test_neuer_verkauf_ist_verkauft(client, db, make_article):
    a = make_article(quantity=3, listing_price=10)
    client.post(f"/articles/{a.id}/sell",
                data={"quantity": "1", "sold_price": "10", "shipping_payer": "Käufer"})
    db.refresh(a)
    assert a.sales[0].fulfillment == "Verkauft"


def test_verkauf_mit_versanddatum_startet_als_versendet(client, db, make_article):
    a = make_article(quantity=3, listing_price=10)
    client.post(f"/articles/{a.id}/sell",
                data={"quantity": "1", "sold_price": "10", "shipping_payer": "Käufer",
                      "shipped_at": "2026-07-10"})
    db.refresh(a)
    assert a.sales[0].fulfillment == "Versendet"


# --- Manuelle Schritte ------------------------------------------------------
def test_schritte_vorwaerts_schalten(client, db):
    a, s = _artikel_mit_verkauf(db)
    for ziel in ("Bezahlt", "Versendet", "Abgeschlossen"):
        client.post(f"/sales/{s.id}/fulfillment", data={"to": ziel, "back": "/sales"})
        db.refresh(s)
        assert s.fulfillment == ziel
    assert s.paid_at is not None
    assert s.shipped_at is not None


def test_advance_schaltet_nie_rueckwaerts(db):
    _, s = _artikel_mit_verkauf(db)
    services.advance_fulfillment(s, "Versendet")
    assert services.advance_fulfillment(s, "Bezahlt") is False   # rückwärts ignoriert
    assert s.fulfillment == "Versendet"


def test_manuell_darf_rueckwaerts(db):
    _, s = _artikel_mit_verkauf(db)
    services.set_fulfillment(db, s, "Versendet")
    services.set_fulfillment(db, s, "Bezahlt")     # Korrektur zurück
    assert s.fulfillment == "Bezahlt"


# --- Storno / Retoure -------------------------------------------------------
def test_storno_bucht_bestand_zurueck(client, db):
    a, s = _artikel_mit_verkauf(db, qty=5, verkauft=2)
    assert a.quantity == 3
    client.post(f"/sales/{s.id}/fulfillment",
                data={"to": FULFILLMENT_CANCELLED, "back": "/sales"})
    db.refresh(a); db.refresh(s)
    assert s.is_cancelled
    assert s.cancelled_at is not None
    assert a.quantity == 5                          # 2 Stück zurück


def test_storno_zaehlt_nicht_im_umsatz(db):
    a, s = _artikel_mit_verkauf(db, verkauft=1)
    assert a.revenue == 20 and a.total_profit is not None
    services.set_fulfillment(db, s, FULFILLMENT_CANCELLED)
    db.commit()
    assert a.revenue == 0
    assert a.total_profit is None
    assert a.sold_quantity == 0


def test_storno_aufheben_bucht_wieder_aus(db):
    a, s = _artikel_mit_verkauf(db, qty=5, verkauft=2)
    services.set_fulfillment(db, s, FULFILLMENT_CANCELLED)
    db.commit()
    assert a.quantity == 5
    services.set_fulfillment(db, s, "Verkauft")     # Storno rückgängig
    db.commit()
    assert a.quantity == 3                           # wieder ausgebucht
    assert not s.is_cancelled


def test_ausverkauft_wird_nach_storno_wieder_angeboten(client, db):
    a, s = _artikel_mit_verkauf(db, qty=1, verkauft=1)
    assert a.status == "Verkauft" and a.quantity == 0
    client.post(f"/sales/{s.id}/fulfillment",
                data={"to": FULFILLMENT_CANCELLED, "back": "/sales"})
    db.refresh(a)
    assert a.quantity == 1
    assert a.status == "Angeboten"


# --- Automatik --------------------------------------------------------------
def test_tracking_zugestellt_setzt_status(db, monkeypatch):
    monkeypatch.setattr(config, "DHL_API_KEY", "test")
    _, s = _artikel_mit_verkauf(db, tracking_carrier="DHL", tracking_number="00340001")

    def fake_track(carrier, number):
        return carriers.TrackingResult(status=carriers.ZUGESTELLT, text="Zugestellt",
                                       delivered_at=datetime.now(timezone.utc))
    monkeypatch.setattr(carriers, "track", fake_track)

    maintenance.update_tracking()
    db.refresh(s)
    assert s.fulfillment == "Zugestellt"


def test_auto_abschluss_nach_frist(db, monkeypatch):
    monkeypatch.setattr(config, "AUTO_COMPLETE_DAYS", 14)
    _, s = _artikel_mit_verkauf(db)
    s.fulfillment = "Zugestellt"
    s.tracking_delivered_at = datetime.now(timezone.utc) - timedelta(days=20)
    db.commit()

    assert maintenance.auto_complete_sales() == 1
    db.refresh(s)
    assert s.fulfillment == "Abgeschlossen"


def test_auto_abschluss_nicht_zu_frueh(db, monkeypatch):
    monkeypatch.setattr(config, "AUTO_COMPLETE_DAYS", 14)
    _, s = _artikel_mit_verkauf(db)
    s.fulfillment = "Zugestellt"
    s.tracking_delivered_at = datetime.now(timezone.utc) - timedelta(days=3)
    db.commit()
    assert maintenance.auto_complete_sales() == 0
    db.refresh(s)
    assert s.fulfillment == "Zugestellt"


def test_backfill_setzt_bestand_auf_abgeschlossen(db):
    _, s = _artikel_mit_verkauf(db)
    s.fulfillment = ""            # wie ein Bestandsverkauf vor dem Update
    db.commit()
    maintenance.backfill_fulfillment()
    db.refresh(s)
    assert s.fulfillment == "Abgeschlossen"


# --- Dashboard-Aufgaben -----------------------------------------------------
def test_dashboard_listet_zu_versenden(client, db):
    _artikel_mit_verkauf(db)     # Status Verkauft
    html = client.get("/").text
    assert "Zu erledigen" in html


def test_abgeschlossener_verkauf_ist_keine_aufgabe(client, db):
    _, s = _artikel_mit_verkauf(db)
    s.fulfillment = "Abgeschlossen"
    db.commit()
    assert "Zu erledigen" not in client.get("/").text


# --- Verkaufsliste-Filter ---------------------------------------------------
def test_sales_filter_nach_status(client, db):
    _, s1 = _artikel_mit_verkauf(db)                         # Verkauft
    a2, s2 = _artikel_mit_verkauf(db)
    s2.fulfillment = "Abgeschlossen"; db.commit()
    html = client.get("/sales?status=Verkauft").text
    assert f"LS-{s2.id:05d}" not in html or "Abgeschlossen" not in html
    # der offene Verkauf ist sichtbar, der abgeschlossene rausgefiltert
    assert "🛒 Verkauft" in html
