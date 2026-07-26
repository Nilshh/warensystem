"""Packliste: offene Bestellungen sammeln, packen, versenden."""
from app.models import Article, Sale, StorageLocation


def _verkauf(client, db, make_article, buyer="Max", status="Verkauft", **kw):
    a = make_article(title=kw.pop("title", "Ware"), quantity=1, listing_price=10)
    client.post(f"/articles/{a.id}/sell",
                data={"quantity": "1", "sold_price": "10", "shipping_payer": "Käufer",
                      "buyer_name": buyer, "shipping_method": kw.get("shipping_method", "DHL")})
    s = db.query(Sale).order_by(Sale.id.desc()).first()
    if status != "Verkauft":
        s.fulfillment = status
        db.commit()
    return a, s


def test_packliste_zeigt_offene_bestellungen(client, db, make_article):
    _verkauf(client, db, make_article, buyer="Anna", title="Lok")
    html = client.get("/packlist").text
    assert "Lok" in html
    assert "Anna" in html


def test_packliste_gruppiert_nach_kaeufer(client, db, make_article):
    _verkauf(client, db, make_article, buyer="Berta", title="Waggon B")
    _verkauf(client, db, make_article, buyer="Anton", title="Waggon A")
    html = client.get("/packlist").text
    # Käufer alphabetisch: Anton vor Berta
    assert html.index("Anton") < html.index("Berta")


def test_packliste_zeigt_lagerplatz(client, db, make_article):
    client.post("/storage/new", data={"area": "Keller", "shelf": "A", "bin": "3"})
    loc = db.query(StorageLocation).one()
    a = make_article(title="Trafo", quantity=1, listing_price=10, storage_location_id=loc.id)
    client.post(f"/articles/{a.id}/sell",
                data={"quantity": "1", "sold_price": "10", "shipping_payer": "Käufer", "buyer_name": "X"})
    assert "Keller, Regal A, Fach 3" in client.get("/packlist").text


def test_packliste_ignoriert_versendete(client, db, make_article):
    _verkauf(client, db, make_article, buyer="Anna", title="Schon-Weg", status="Versendet")
    _verkauf(client, db, make_article, buyer="Anna", title="Noch-Da", status="Verkauft")
    html = client.get("/packlist").text
    assert "Noch-Da" in html
    assert "Schon-Weg" not in html


def test_packliste_zeigt_bezahlte(client, db, make_article):
    _verkauf(client, db, make_article, buyer="Anna", title="Bezahlt-Artikel", status="Bezahlt")
    assert "Bezahlt-Artikel" in client.get("/packlist").text


def test_sammelversand_markiert_versendet(client, db, make_article):
    _, s1 = _verkauf(client, db, make_article, buyer="Anna", title="A")
    _, s2 = _verkauf(client, db, make_article, buyer="Berta", title="B")

    r = client.post("/packlist/ship", data={"ids": [str(s1.id), str(s2.id)]},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "msg=" in r.headers["location"]
    db.refresh(s1); db.refresh(s2)
    assert s1.fulfillment == "Versendet"
    assert s2.fulfillment == "Versendet"


def test_sammelversand_gibt_lagerplatz_frei(client, db, make_article):
    client.post("/storage/new", data={"area": "Keller", "shelf": "A", "bin": "3"})
    loc = db.query(StorageLocation).one()
    a = make_article(title="Trafo", quantity=1, listing_price=10, storage_location_id=loc.id)
    client.post(f"/articles/{a.id}/sell",
                data={"quantity": "1", "sold_price": "10", "shipping_payer": "Käufer", "buyer_name": "X"})
    s = db.query(Sale).one()
    client.post("/packlist/ship", data={"ids": [str(s.id)]})
    db.refresh(a)
    assert a.storage_location == ""          # nach Versand frei


def test_sammelversand_ohne_auswahl(client, db, make_article):
    _verkauf(client, db, make_article, buyer="Anna")
    r = client.post("/packlist/ship", data={}, follow_redirects=False)
    assert "Keine" in r.headers["location"]


def test_packliste_leer(client):
    assert "alles versendet" in client.get("/packlist").text
