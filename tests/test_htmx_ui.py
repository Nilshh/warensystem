"""UI-Umbau (htmx): Fragmente, Einlagern am Lagerort, Live-Suche.

Progressive Enhancement: Ohne HX-Request-Header verhalten sich alle Routen
wie bisher (Redirect bzw. ganze Seite) — mit Header kommt nur das Fragment.
"""


def _make_sale(client, make_article, db):
    from app.models import Sale
    a = make_article(title="Lok BR 218", quantity=3, listing_price=99.0)
    r = client.post(f"/articles/{a.id}/sell", data={
        "quantity": 1, "sold_price": "89,00", "buyer_name": "Max",
    }, follow_redirects=False)
    assert r.status_code == 303
    return db.query(Sale).order_by(Sale.id.desc()).first()


# ---------------------------------------------------------------------------
# Abwicklung
# ---------------------------------------------------------------------------

def test_fulfillment_klassisch_bleibt_redirect(client, db, make_article):
    sale = _make_sale(client, make_article, db)
    r = client.post(f"/sales/{sale.id}/fulfillment",
                    data={"to": "Bezahlt", "back": "/sales"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "/sales" in r.headers["location"]


def test_fulfillment_htmx_liefert_fragment(client, db, make_article):
    sale = _make_sale(client, make_article, db)
    r = client.post(f"/sales/{sale.id}/fulfillment",
                    data={"to": "Bezahlt", "back": "/sales", "show_tracking": "1"},
                    headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert f'id="fulfil-{sale.id}"' in r.text          # ersetzt sich selbst
    assert "Bezahlt" in r.text                          # neuer Status sichtbar
    assert "<html" not in r.text                        # nur Fragment, keine Seite
    assert "Versendet" in r.text                        # nächster Schritt angeboten


def test_verkaufsliste_zeigt_statusknoepfe(client, db, make_article):
    _make_sale(client, make_article, db)
    r = client.get("/sales")
    assert "fulfil-actions" in r.text                   # Knöpfe jetzt auch in der Liste
    assert "hx-post" in r.text


# ---------------------------------------------------------------------------
# Lager: Einlagern am Lagerort
# ---------------------------------------------------------------------------

def test_einlagern_ordnet_artikel_zu(client, db, make_article):
    a = make_article(title="Waggon", quantity=1)
    r = client.post("/storage/location/assign", data={
        "area": "Keller", "shelf": "A", "bin": "3", "article_no": a.article_no,
    }, follow_redirects=False)
    assert r.status_code == 303
    db.refresh(a)
    assert (a.storage_area, a.storage_shelf, a.storage_bin) == ("Keller", "A", "3")


def test_einlagern_htmx_liefert_fragment_mit_meldung(client, db, make_article):
    a = make_article(title="Waggon", quantity=1)
    r = client.post("/storage/location/assign", data={
        "area": "Keller", "shelf": "A", "bin": "3", "article_no": a.article_no,
    }, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert 'id="loc-articles"' in r.text
    assert "eingelagert" in r.text
    assert a.title in r.text                            # Liste enthält den Artikel


def test_einlagern_nur_ziffern_wird_zur_artikelnummer(client, db, make_article):
    a = make_article(title="Signal", quantity=1)
    nummer = a.article_no.split("-")[-1].lstrip("0")    # "WA-00007" -> "7"
    r = client.post("/storage/location/assign", data={
        "area": "Boden", "shelf": "", "bin": "", "article_no": nummer,
    }, headers={"HX-Request": "true"})
    assert "eingelagert" in r.text
    db.refresh(a)
    assert a.storage_area == "Boden"


def test_einlagern_unbekannte_nummer_gibt_fehler(client, db, make_article):
    r = client.post("/storage/location/assign", data={
        "area": "Keller", "shelf": "A", "bin": "3", "article_no": "WA-99999",
    }, headers={"HX-Request": "true"})
    assert "Kein Artikel" in r.text


def test_umlagern_nennt_alten_platz(client, db, make_article):
    a = make_article(title="Trafo", quantity=1)
    a.storage_area, a.storage_shelf, a.storage_bin = "Keller", "A", "1"
    db.commit()
    r = client.post("/storage/location/assign", data={
        "area": "Boden", "shelf": "B", "bin": "2", "article_no": a.article_no,
    }, headers={"HX-Request": "true"})
    assert "umgelagert" in r.text


# ---------------------------------------------------------------------------
# Artikelliste: Live-Suche
# ---------------------------------------------------------------------------

def test_artikelliste_htmx_liefert_nur_ergebnisliste(client, db, make_article):
    make_article(title="Diesellok", quantity=1)
    r = client.get("/articles?q=Diesel&status=", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert 'id="articles-results"' in r.text
    assert "Diesellok" in r.text
    assert "<html" not in r.text                        # Fragment, keine ganze Seite


def test_artikelliste_klassisch_bleibt_ganze_seite(client, db, make_article):
    make_article(title="Diesellok", quantity=1)
    r = client.get("/articles?q=Diesel&status=")
    assert "<html" in r.text
    assert "Diesellok" in r.text
