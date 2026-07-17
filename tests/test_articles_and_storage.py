"""Artikelliste (Filter, Massenbearbeitung) und Lagerverwaltung."""
from app.models import Article, StorageLocation


# --- Filter & Sortierung ----------------------------------------------------
def test_liste_filtert_standardmaessig_auf_angeboten(client, make_article):
    make_article(title="Sichtbar", status="Angeboten")
    make_article(title="Entwurfsartikel", status="Entwurf")
    html = client.get("/articles").text
    assert "Sichtbar" in html
    assert ">Entwurfsartikel<" not in html


def test_alle_status_zeigt_alles(client, make_article):
    make_article(title="Sichtbar", status="Angeboten")
    make_article(title="Entwurfsartikel", status="Entwurf")
    html = client.get("/articles?status=").text
    assert "Sichtbar" in html and "Entwurfsartikel" in html


def test_standardsortierung_nach_artikelnummer(client, make_article):
    make_article(title="Zebra")
    make_article(title="Apfel")
    html = client.get("/articles").text
    assert html.index("WA-00001") < html.index("WA-00002")


def test_kategorie_filter(client, make_article):
    make_article(title="Handy1", category="Handys")
    make_article(title="Laptop1", category="Laptops")
    html = client.get("/articles?category=Handys").text
    assert "Handy1" in html and ">Laptop1<" not in html


def test_suche_findet_ueber_lagerplatz(client, db, make_article):
    client.post("/storage/new", data={"area": "Dachboden", "shelf": "B", "bin": "1"})
    loc = db.query(StorageLocation).one()
    make_article(title="Gelagert", storage_location_id=loc.id)
    assert "Gelagert" in client.get("/articles?q=Dachboden").text


# --- Massenbearbeitung ------------------------------------------------------
def test_bulk_status(client, db, make_article):
    a1, a2 = make_article(title="A1"), make_article(title="A2")
    client.post("/articles/bulk-status",
                data={"ids": [str(a1.id), str(a2.id)], "new_status": "Reserviert"})
    db.expire_all()
    assert {db.get(Article, a1.id).status, db.get(Article, a2.id).status} == {"Reserviert"}


def test_bulk_kategorie(client, db, make_article):
    a = make_article(title="A1", category="Alt")
    client.post("/articles/bulk-category", data={"ids": [str(a.id)], "new_category": "Neu"})
    db.expire_all()
    assert db.get(Article, a.id).category == "Neu"


def test_bulk_lagerplatz(client, db, make_article):
    client.post("/storage/new", data={"area": "Keller", "shelf": "A", "bin": "3"})
    loc = db.query(StorageLocation).one()
    a = make_article(title="A1")
    client.post("/articles/bulk-storage",
                data={"ids": [str(a.id)], "storage_location_id": str(loc.id)})
    db.expire_all()
    assert db.get(Article, a.id).storage_location == "Keller, Regal A, Fach 3"


def test_bulk_ohne_auswahl_aendert_nichts(client, db, make_article):
    a = make_article(title="A1", status="Angeboten")
    client.post("/articles/bulk-status", data={"new_status": "Verkauft"})
    db.expire_all()
    assert db.get(Article, a.id).status == "Angeboten"


# --- Lagerverwaltung --------------------------------------------------------
def test_lagerplatz_anlegen_ohne_duplikate(client, db):
    for _ in range(2):
        client.post("/storage/new", data={"area": "Keller", "shelf": "A", "bin": "3"})
    assert db.query(StorageLocation).count() == 1


def test_lagerplatz_umbenennen_zieht_artikel_mit(client, db, make_article):
    client.post("/storage/new", data={"area": "Keller", "shelf": "A", "bin": "3"})
    loc = db.query(StorageLocation).one()
    a = make_article(title="Umzug", storage_location_id=loc.id)

    client.post(f"/storage/{loc.id}/edit", data={"area": "Keller", "shelf": "A", "bin": "9"})
    db.expire_all()
    assert db.get(Article, a.id).storage_location == "Keller, Regal A, Fach 9"


def test_belegter_lagerplatz_nicht_loeschbar(client, db, make_article):
    client.post("/storage/new", data={"area": "Keller", "shelf": "A", "bin": "3"})
    loc = db.query(StorageLocation).one()
    make_article(title="Belegt", storage_location_id=loc.id)

    r = client.post(f"/storage/{loc.id}/delete", follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert db.query(StorageLocation).count() == 1


def test_leerer_lagerplatz_loeschbar(client, db):
    client.post("/storage/new", data={"area": "Leer", "shelf": "", "bin": ""})
    loc = db.query(StorageLocation).one()
    client.post(f"/storage/{loc.id}/delete")
    assert db.query(StorageLocation).count() == 0


def test_lageruebersicht_zeigt_anzahl_ohne_inhalt(client, db, make_article):
    client.post("/storage/new", data={"area": "Keller", "shelf": "A", "bin": "3"})
    loc = db.query(StorageLocation).one()
    make_article(title="Geheimer Inhalt", storage_location_id=loc.id)

    html = client.get("/storage").text
    assert "Keller, Regal A, Fach 3" in html
    assert "Geheimer Inhalt" not in html          # Inhalt erst auf der Detailseite
    assert "Geheimer Inhalt" in client.get(
        "/storage/location?area=Keller&shelf=A&bin=3").text
