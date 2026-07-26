"""QR-Codes und Etiketten: die Endpunkte müssen echtes SVG liefern."""


def test_artikel_qr_liefert_svg(client, make_article):
    a = make_article(title="Mit QR")
    r = client.get(f"/articles/{a.id}/qr.svg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/svg+xml"
    assert "<svg" in r.text and "<path" in r.text


def test_artikel_etikett_enthaelt_qr(client, make_article):
    a = make_article(title="Mit QR")
    r = client.get(f"/articles/{a.id}/label")
    assert r.status_code == 200
    assert "<svg" in r.text
    assert a.article_no in r.text


def test_artikel_qr_404_bei_unbekannt(client):
    assert client.get("/articles/99999/qr.svg").status_code == 404


def test_lager_qr_liefert_svg(client):
    r = client.get("/storage/qr.svg?area=Keller&shelf=A&bin=3")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/svg+xml"
    assert "<svg" in r.text


def test_lager_etikett_enthaelt_qr(client):
    r = client.get("/storage/label?area=Keller&shelf=A&bin=3")
    assert r.status_code == 200
    assert "<svg" in r.text
    assert "Keller, Regal A, Fach 3" in r.text
