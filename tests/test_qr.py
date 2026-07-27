"""QR-Codes und Etiketten.

Die Online-Vorschau (/qr.svg) liefert echtes SVG. Die Druckseiten dagegen
betten den QR-Code als PNG-Data-URI ein: Inline-SVG (mit <?xml?>-Prolog und
mm-Maßen) ließ den Browser beim Drucken hängen oder einen weißen Zettel
ausgeben – deshalb dürfen die Etiketten kein Inline-SVG mehr enthalten.
"""


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
    # QR als PNG-Data-URI, kein Inline-SVG/XML (sonst weißer Zettel/Browser hängt)
    assert "data:image/png;base64," in r.text
    assert "<svg" not in r.text and "<?xml" not in r.text
    assert a.article_no in r.text


def test_sammeletikett_enthaelt_png_qr(client, make_article):
    a = make_article(title="Erster")
    b = make_article(title="Zweiter")
    r = client.post("/articles/bulk-labels", data={"ids": [str(a.id), str(b.id)]})
    assert r.status_code == 200
    assert r.text.count("data:image/png;base64,") == 2
    assert "<svg" not in r.text and "<?xml" not in r.text


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
    assert "data:image/png;base64," in r.text
    assert "<svg" not in r.text and "<?xml" not in r.text
    assert "Keller, Regal A, Fach 3" in r.text
