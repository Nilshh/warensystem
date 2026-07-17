"""Verkaufslogik: Gewinnberechnung, Bestandsführung, Korrekturen."""
from app.models import Sale, StorageLocation


# --- Gewinnberechnung -------------------------------------------------------
def test_gewinn_kaeufer_zahlt_versand_ist_neutral():
    sale = Sale(quantity=1, sold_price=100, unit_purchase_cost=40, fees=11,
                shipping_cost=5.49, shipping_payer="Käufer")
    # Versand ist durchlaufend: 100 - 40 - 11
    assert sale.profit == 49.0


def test_gewinn_verkaeufer_zahlt_versand_mindert_gewinn():
    sale = Sale(quantity=1, sold_price=100, unit_purchase_cost=40, fees=11,
                shipping_cost=5.49, shipping_payer="Verkäufer")
    assert sale.profit == 43.51


def test_gewinn_rechnet_einkauf_mal_stueckzahl():
    sale = Sale(quantity=3, sold_price=90, unit_purchase_cost=10, fees=0,
                shipping_payer="Käufer")
    assert sale.profit == 60.0


def test_marge_in_prozent():
    sale = Sale(quantity=1, sold_price=200, unit_purchase_cost=100, fees=0,
                shipping_payer="Käufer")
    assert sale.margin == 50.0


def test_marge_ohne_erloes_ist_none():
    assert Sale(quantity=1, sold_price=0).margin is None


# --- Bestandsführung beim Verkauf ------------------------------------------
def test_verkauf_reduziert_bestand(client, db, make_article):
    a = make_article(quantity=5, purchase_cost=2, listing_price=10)
    client.post(f"/articles/{a.id}/sell",
                data={"quantity": "2", "sold_price": "20", "shipping_payer": "Käufer"})
    db.refresh(a)
    assert a.quantity == 3
    assert a.status == "Angeboten"          # noch Bestand -> weiter angeboten
    assert len(a.sales) == 1


def test_ausverkauf_setzt_status_und_gibt_lagerplatz_frei(client, db, make_article):
    client.post("/storage/new", data={"area": "Keller", "shelf": "A", "bin": "3"})
    loc_id = db.query(StorageLocation).one().id
    a = make_article(quantity=1, storage_location_id=loc_id)
    assert a.storage_location == "Keller, Regal A, Fach 3"

    client.post(f"/articles/{a.id}/sell",
                data={"quantity": "1", "sold_price": "10", "shipping_payer": "Käufer"})
    db.refresh(a)
    assert a.quantity == 0
    assert a.status == "Verkauft"
    assert a.storage_location == ""         # Platz ist wieder frei


def test_verkauf_ueber_bestand_wird_abgelehnt(client, db, make_article):
    a = make_article(quantity=2)
    r = client.post(f"/articles/{a.id}/sell",
                    data={"quantity": "5", "sold_price": "50"}, follow_redirects=False)
    db.refresh(a)
    assert "error=" in r.headers["location"]
    assert a.quantity == 2                  # unverändert
    assert a.sales == []


# --- Korrektur & Löschen ----------------------------------------------------
def test_korrektur_der_stueckzahl_bucht_bestand_zurueck(client, db, make_article):
    a = make_article(quantity=5, purchase_cost=2)
    client.post(f"/articles/{a.id}/sell",
                data={"quantity": "2", "sold_price": "20", "shipping_payer": "Käufer"})
    db.refresh(a)
    sale = a.sales[0]

    client.post(f"/sales/{sale.id}/edit",
                data={"quantity": "1", "sold_price": "10", "unit_purchase_cost": "2",
                      "fees": "0", "shipping_payer": "Käufer"})
    db.refresh(a)
    db.refresh(sale)
    assert sale.quantity == 1
    assert a.quantity == 4                  # 1 Stück zurück im Bestand


def test_korrektur_ueber_verfuegbaren_bestand_wird_abgelehnt(client, db, make_article):
    a = make_article(quantity=1)
    client.post(f"/articles/{a.id}/sell",
                data={"quantity": "1", "sold_price": "10", "shipping_payer": "Käufer"})
    db.refresh(a)
    sale = a.sales[0]
    r = client.post(f"/sales/{sale.id}/edit",
                    data={"quantity": "9", "sold_price": "90"}, follow_redirects=False)
    db.refresh(sale)
    assert "error=" in r.headers["location"]
    assert sale.quantity == 1


def test_verkauf_loeschen_bucht_bestand_zurueck_und_reaktiviert(client, db, make_article):
    a = make_article(quantity=1)
    client.post(f"/articles/{a.id}/sell",
                data={"quantity": "1", "sold_price": "10", "shipping_payer": "Käufer"})
    db.refresh(a)
    assert a.status == "Verkauft" and a.quantity == 0
    sale_id = a.sales[0].id

    client.post(f"/sales/{sale_id}/delete")
    db.refresh(a)
    assert a.quantity == 1
    assert a.status == "Angeboten"          # wieder verfügbar
    assert a.sales == []


# --- Artikel-Kennzahlen -----------------------------------------------------
def test_artikel_summiert_umsatz_und_gewinn(client, db, make_article):
    a = make_article(quantity=3, purchase_cost=2)
    client.post(f"/articles/{a.id}/sell",
                data={"quantity": "1", "sold_price": "10", "fees": "1.10",
                      "shipping_payer": "Käufer"})
    client.post(f"/articles/{a.id}/sell",
                data={"quantity": "2", "sold_price": "20", "fees": "2.20",
                      "shipping_payer": "Käufer"})
    db.refresh(a)
    assert a.sold_quantity == 3
    assert a.revenue == 30.0
    # (10-2-1.10) + (20-4-2.20)
    assert a.total_profit == 20.7


def test_artikel_ohne_verkauf_hat_keinen_gewinn(make_article):
    assert make_article().total_profit is None
