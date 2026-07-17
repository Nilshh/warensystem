"""Gemeinsame Test-Fixtures.

Wichtig: DATA_DIR/BACKUP_DIR müssen gesetzt sein, BEVOR `app.*` importiert wird —
die Konfiguration (und damit der DB-Pfad) wird beim Import ausgewertet.
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="warensystem-tests-")
os.environ["DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["BACKUP_DIR"] = os.path.join(_TMP, "backups")
os.environ["AUTO_BACKUP_HOURS"] = "0"      # Hintergrund-Tasks im Test aus
os.environ["ARCHIVE_AFTER_DAYS"] = "7"
os.environ["BASE_URL"] = "http://wa.test"

import pytest  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app import main as m  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Article, ArticleImage, Sale, StorageLocation  # noqa: E402


@pytest.fixture
def client():
    return TestClient(m.app)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def clean_db():
    """Vor jedem Test eine leere Datenbank."""
    session = SessionLocal()
    for model in (Sale, ArticleImage, Article, StorageLocation):
        session.query(model).delete()
    session.commit()
    session.close()
    yield


@pytest.fixture
def make_article(client, db):
    """Legt einen Artikel über das Formular an und gibt ihn zurück."""
    def _make(**kwargs):
        data = {
            "title": kwargs.pop("title", "Testartikel"),
            "status": kwargs.pop("status", "Angeboten"),
            "quantity": str(kwargs.pop("quantity", 1)),
            "purchase_cost": str(kwargs.pop("purchase_cost", 0)),
            "listing_price": str(kwargs.pop("listing_price", 0)),
        }
        data.update({k: str(v) for k, v in kwargs.items()})
        client.post("/articles/new", data=data)
        return db.query(Article).filter_by(title=data["title"]).one()
    return _make
