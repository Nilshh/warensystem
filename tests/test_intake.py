"""Wareneingang: Konvolut in Einzelartikel aufteilen."""
import pytest

from app.services import allocate_costs, parse_intake_lines
from app.models import Article


# --- Kostenverteilung (reine Logik) ----------------------------------------
def test_gleichmaessige_verteilung():
    assert allocate_costs(90, None, 3) == [30.0, 30.0, 30.0]


def test_anteilige_verteilung_nach_wert():
    # Gesamtwert 100 -> Anteile 20% / 80%
    assert allocate_costs(50, [20, 80], 2) == [10.0, 40.0]


def test_summe_stimmt_trotz_rundung():
    # 100 / 3 = 33,333… -> Rundungsdifferenz darf nicht verloren gehen
    shares = allocate_costs(100, None, 3)
    assert sum(shares) == 100.0
    assert shares == [33.33, 33.33, 33.34]


@pytest.mark.parametrize("total,weights,count", [
    (120.0, None, 7),
    (99.99, [1, 2, 3], 3),
    (250.0, [33, 33, 34], 3),
    (10.0, [1, 1, 1, 1, 1, 1], 6),
])
def test_summe_stimmt_immer(total, weights, count):
    assert sum(allocate_costs(total, weights, count)) == pytest.approx(total, abs=0.005)


def test_leere_liste():
    assert allocate_costs(100, None, 0) == []


def test_gewichte_ohne_wert_fallen_auf_gleichverteilung_zurueck():
    assert allocate_costs(60, [0, 0, 0], 3) == [20.0, 20.0, 20.0]


# --- Eingabe zerlegen -------------------------------------------------------
def test_zeilen_ohne_preis():
    assert parse_intake_lines("Lok\nWagen") == [("Lok", None), ("Wagen", None)]


def test_zeilen_mit_preis():
    assert parse_intake_lines("Lok | 70\nWagen | 20") == [("Lok", 70.0), ("Wagen", 20.0)]


def test_deutsches_komma_und_leerzeilen():
    assert parse_intake_lines("  Lok | 49,90  \n\n\nWagen|5") == [("Lok", 49.9), ("Wagen", 5.0)]


def test_leerer_titel_wird_aufgefangen():
    assert parse_intake_lines(" | 10") == [("Ohne Titel", 10.0)]


# --- Ende-zu-Ende -----------------------------------------------------------
def test_konvolut_anlegen_verteilt_anteilig(client, db):
    r = client.post("/intake", data={
        "total_cost": "50",
        "items": "Lok | 80\nWagen | 20",
        "category": "Modellbahn",
    })
    assert r.status_code == 200
    lok = db.query(Article).filter_by(title="Lok").one()
    wagen = db.query(Article).filter_by(title="Wagen").one()
    # 80/100 bzw. 20/100 von 50 €
    assert lok.purchase_cost == 40.0
    assert wagen.purchase_cost == 10.0
    assert lok.listing_price == 80.0
    assert lok.category == "Modellbahn"
    assert lok.status == "Entwurf"
    assert lok.quantity == 1
    assert lok.article_no and wagen.article_no != lok.article_no


def test_konvolut_ohne_preise_verteilt_gleichmaessig(client, db):
    client.post("/intake", data={"total_cost": "90", "items": "A\nB\nC"})
    kosten = sorted(a.purchase_cost for a in db.query(Article).all())
    assert kosten == [30.0, 30.0, 30.0]


def test_konvolut_teilpreise_fallen_auf_gleichverteilung_zurueck(client, db):
    # Nur eine Position hat einen Preis -> gleichmäßig (sonst wäre es willkürlich)
    client.post("/intake", data={"total_cost": "60", "items": "A | 50\nB\nC"})
    kosten = sorted(a.purchase_cost for a in db.query(Article).all())
    assert kosten == [20.0, 20.0, 20.0]


def test_konvolut_ohne_positionen_wird_abgelehnt(client, db):
    r = client.post("/intake", data={"total_cost": "50", "items": "  \n\n"},
                    follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert db.query(Article).count() == 0


def test_konvolut_mit_lagerplatz(client, db):
    from app.models import StorageLocation
    client.post("/storage/new", data={"area": "Keller", "shelf": "A", "bin": "3"})
    loc = db.query(StorageLocation).one()
    client.post("/intake", data={"total_cost": "10", "items": "A\nB",
                                 "storage_location_id": str(loc.id)})
    assert all(a.storage_location == "Keller, Regal A, Fach 3"
               for a in db.query(Article).all())
