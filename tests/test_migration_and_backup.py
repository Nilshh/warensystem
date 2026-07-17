"""Datenmigration (Alt-Verkäufe) und Sicherung/Wiederherstellung."""
import io
import zipfile

from sqlalchemy import text

from app import backup, maintenance, services
from app.models import Article


# --- Migration der Alt-Verkäufe --------------------------------------------
_LEGACY_DDL = {
    "sold_price": "FLOAT DEFAULT 0", "fees": "FLOAT DEFAULT 0",
    "sale_platform": "VARCHAR(30) DEFAULT ''", "buyer_name": "VARCHAR(150) DEFAULT ''",
    "buyer_address": "TEXT DEFAULT ''", "payment_method": "VARCHAR(80) DEFAULT ''",
    "tracking_carrier": "VARCHAR(80) DEFAULT ''", "tracking_number": "VARCHAR(100) DEFAULT ''",
    "order_date": "DATETIME", "shipped_at": "DATETIME", "sold_at": "DATETIME",
}


def _simulate_old_database(db):
    """Baut die Verkaufsspalten am Artikel nach, wie sie in alten DBs existieren.

    Sie sind nicht mehr Teil des Modells, können aber über ein altes Backup
    zurückkommen — genau dafür gibt es die Migration.
    """
    vorhanden = {r[1] for r in db.execute(text("PRAGMA table_info(articles)"))}
    for name, ddl in _LEGACY_DDL.items():
        if name not in vorhanden:
            db.execute(text(f"ALTER TABLE articles ADD COLUMN {name} {ddl}"))
    db.commit()


def _legacy_sold_article(db) -> Article:
    """Artikel im alten Stil: Verkaufsdaten hängen am Artikel, kein Sale."""
    _simulate_old_database(db)
    a = Article(
        title="Alt-Verkauf", status="Verkauft", quantity=1,
        purchase_cost=200, listing_price=350,
        shipping_method="DHL", shipping_cost=5.49, shipping_payer="Käufer",
    )
    db.add(a)
    db.commit()
    db.execute(text(
        "UPDATE articles SET sold_price=340, fees=37.40, buyer_name='Max M.', "
        "sale_platform='eBay', sold_at='2026-03-15 00:00:00' WHERE id=:id"
    ), {"id": a.id})
    db.commit()
    return a


def test_migration_uebernimmt_alt_verkauf_in_historie(db):
    a = _legacy_sold_article(db)
    assert maintenance.migrate_legacy_sales() == 1

    db.refresh(a)
    assert len(a.sales) == 1
    sale = a.sales[0]
    assert sale.sold_price == 340
    assert sale.unit_purchase_cost == 200
    assert sale.buyer_name == "Max M."
    assert sale.sold_at.year == 2026
    # Gewinn bleibt exakt erhalten: 340 - 200 - 37.40 (Versand zahlt Käufer)
    assert sale.profit == 102.60
    assert a.quantity == 0          # Einzelstück war verkauft


def test_migration_ist_idempotent(db):
    a = _legacy_sold_article(db)
    maintenance.migrate_legacy_sales()
    assert maintenance.migrate_legacy_sales() == 0     # zweiter Lauf ändert nichts
    db.refresh(a)
    assert len(a.sales) == 1


def test_migration_laesst_offene_artikel_in_ruhe(db):
    a = Article(title="Offen", status="Angeboten", quantity=1, purchase_cost=50)
    db.add(a)
    db.commit()
    maintenance.migrate_legacy_sales()
    db.refresh(a)
    assert a.sales == []
    assert a.quantity == 1


# --- Artikelnummern ---------------------------------------------------------
def test_artikelnummer_wird_automatisch_vergeben(make_article):
    a = make_article(title="Mit Nummer")
    assert a.article_no.startswith("WA-")


def test_duplikat_bekommt_eigene_nummer(client, db, make_article):
    a = make_article(title="Original")
    client.post(f"/articles/{a.id}/duplicate")
    kopie = db.query(Article).filter(Article.id != a.id).one()
    assert kopie.article_no != a.article_no
    assert kopie.quantity == 1
    assert kopie.status == "Entwurf"


def test_backfill_vergibt_fehlende_nummern(db):
    a = Article(title="Ohne Nummer", article_no="", quantity=1)
    db.add(a)
    db.commit()
    maintenance.backfill_article_numbers()
    db.refresh(a)
    assert a.article_no == services.make_article_no(a.id)


# --- Backup & Restore -------------------------------------------------------
def test_backup_enthaelt_alle_tabellen(client, db, make_article):
    make_article(title="Im Backup")
    zf = zipfile.ZipFile(io.BytesIO(client.get("/backup.zip").content))
    assert "warensystem.db" in zf.namelist()

    import os
    import sqlite3
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(zf.read("warensystem.db"))
        con = sqlite3.connect(tmp)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
    finally:
        os.unlink(tmp)
    # Auch die später hinzugekommenen Tabellen müssen gesichert werden
    assert {"articles", "sales", "storage_locations", "article_images"} <= tables


def test_restore_stellt_alten_stand_wieder_her(client, db, make_article):
    make_article(title="Vorher")
    gesichert = client.get("/backup.zip").content

    make_article(title="Danach")
    assert db.query(Article).count() == 2

    client.post("/restore", files={"file": ("b.zip", io.BytesIO(gesichert), "application/zip")})
    db.expire_all()
    titel = [a.title for a in db.query(Article).all()]
    assert titel == ["Vorher"]


def test_restore_lehnt_ungueltiges_zip_ab(client):
    r = client.post("/restore", files={"file": ("x.zip", io.BytesIO(b"kein zip"), "application/zip")},
                    follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_auto_backup_rotiert(tmp_path, make_article):
    make_article()
    for _ in range(4):
        backup.write_backup_file(directory=tmp_path, keep=2)
    autos = list(tmp_path.glob("warensystem-auto-*.zip"))
    assert len(autos) <= 2
