"""Auswertungen: Ladenhüter, Plattform-Vergleich, Verkaufsdauer."""
from datetime import datetime, timedelta, timezone

from app.models import Article, Sale


def _alt(tage: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=tage)


# --- Ladenhüter -------------------------------------------------------------
def test_ladenhueter_nur_ab_schwelle(client, db):
    db.add(Article(title="Liegt lange", status="Angeboten", quantity=1,
                   purchase_cost=40, created_at=_alt(200)))
    db.add(Article(title="Frisch rein", status="Angeboten", quantity=1,
                   purchase_cost=10, created_at=_alt(5)))
    db.commit()

    html = client.get("/reports?days=90").text
    assert "Liegt lange" in html
    assert "Frisch rein" not in html


def test_ladenhueter_zeigt_gebundenes_kapital(client, db):
    db.add(Article(title="Teuer", status="Angeboten", quantity=3,
                   purchase_cost=40, created_at=_alt(200)))
    db.commit()
    # 3 Stück x 40 € = 120 €
    assert "120,00 €" in client.get("/reports?days=90").text


def test_ladenhueter_ignoriert_ausverkaufte_und_archivierte(client, db):
    db.add(Article(title="Ausverkauft", status="Verkauft", quantity=0,
                   purchase_cost=40, created_at=_alt(300)))
    db.add(Article(title="Archiviert", status="Archiviert", quantity=2,
                   purchase_cost=40, created_at=_alt(300)))
    db.commit()
    html = client.get("/reports?days=90").text
    assert "Ausverkauft" not in html and "Archiviert</strong>" not in html


def test_ladenhueter_schwelle_einstellbar(client, db):
    db.add(Article(title="Mittelalt", status="Angeboten", quantity=1,
                   purchase_cost=5, created_at=_alt(45)))
    db.commit()
    assert "Mittelalt" in client.get("/reports?days=30").text
    assert "Mittelalt" not in client.get("/reports?days=90").text


# --- Plattform-Vergleich ----------------------------------------------------
def test_plattform_vergleich_summiert_und_rechnet_marge(client, db):
    a = Article(title="Ware", status="Angeboten", quantity=10, purchase_cost=0)
    db.add(a)
    db.commit()
    db.add(Sale(article_id=a.id, quantity=1, sold_price=100, unit_purchase_cost=50,
                fees=0, shipping_payer="Käufer", sale_platform="eBay",
                sold_at=_alt(10)))
    db.add(Sale(article_id=a.id, quantity=1, sold_price=100, unit_purchase_cost=50,
                fees=0, shipping_payer="Käufer", sale_platform="eBay",
                sold_at=_alt(5)))
    db.add(Sale(article_id=a.id, quantity=1, sold_price=50, unit_purchase_cost=10,
                fees=0, shipping_payer="Käufer", sale_platform="Kleinanzeigen",
                sold_at=_alt(3)))
    db.commit()

    html = client.get("/reports").text
    # eBay: Umsatz 200, Gewinn 100 -> Marge 50 %
    assert "200,00 €" in html and "50.0 %" in html
    # Kleinanzeigen: Umsatz 50, Gewinn 40 -> Marge 80 %
    assert "80.0 %" in html


def test_plattform_ohne_angabe_wird_gruppiert(client, db):
    a = Article(title="Ware", status="Angeboten", quantity=5)
    db.add(a)
    db.commit()
    db.add(Sale(article_id=a.id, quantity=1, sold_price=10, sale_platform="",
                shipping_payer="Käufer", sold_at=_alt(1)))
    db.commit()
    assert "ohne Angabe" in client.get("/reports").text


# --- Kategorien -------------------------------------------------------------
def test_kategorie_zeigt_durchschnittliche_verkaufsdauer(client, db):
    a = Article(title="Lok", category="Modellbahn", status="Angeboten",
                quantity=5, created_at=_alt(30))
    db.add(a)
    db.commit()
    # verkauft 10 Tage nach dem Anlegen
    db.add(Sale(article_id=a.id, quantity=1, sold_price=50, unit_purchase_cost=10,
                shipping_payer="Käufer", sold_at=_alt(20)))
    db.commit()

    html = client.get("/reports").text
    assert "Modellbahn" in html
    assert "10 Tage" in html


def test_leere_auswertung_ohne_verkaeufe(client):
    html = client.get("/reports").text
    assert "Noch keine Verkäufe erfasst" in html
