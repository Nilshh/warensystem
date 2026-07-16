"""Leichtgewichtige Auto-Migration für SQLite.

Statt eines schweren Migrations-Frameworks (Alembic) reicht für dieses
Ein-Container-Setup ein einfacher Abgleich: Beim Start werden fehlende Spalten
per ``ALTER TABLE ... ADD COLUMN`` ergänzt. So können neue Felder gefahrlos
dazukommen, ohne die bestehende Datenbank neu anzulegen.

SQLite unterstützt ``ADD COLUMN`` (mit Default) nativ. Für komplexere
Änderungen (Spalten umbenennen/löschen) müsste man später auf echte
Migrationen umsteigen — für den aktuellen Bedarf genügt das hier.
"""
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

# Erwartete Spalten je Tabelle: name -> SQL-Definition (Typ + Default)
_EXPECTED = {
    "articles": {
        "article_no": "VARCHAR(20) DEFAULT ''",
        "quantity": "INTEGER DEFAULT 1",
        "shipping_payer": "VARCHAR(20) DEFAULT 'Käufer'",
        "storage_area": "VARCHAR(80) DEFAULT ''",
        "storage_shelf": "VARCHAR(40) DEFAULT ''",
        "storage_bin": "VARCHAR(40) DEFAULT ''",
        "tags": "VARCHAR(300) DEFAULT ''",
        "sale_platform": "VARCHAR(30) DEFAULT ''",
        "buyer_name": "VARCHAR(150) DEFAULT ''",
        "buyer_address": "TEXT DEFAULT ''",
        "payment_method": "VARCHAR(80) DEFAULT ''",
        "tracking_carrier": "VARCHAR(80) DEFAULT ''",
        "tracking_number": "VARCHAR(100) DEFAULT ''",
        "order_date": "DATETIME",
        "shipped_at": "DATETIME",
        "note": "TEXT DEFAULT ''",
    },
    "article_images": {
        "position": "INTEGER DEFAULT 0",
    },
}


def run_migrations(engine: Engine) -> list[str]:
    """Ergänzt fehlende Spalten. Gibt eine Liste der durchgeführten Änderungen zurück."""
    inspector = inspect(engine)
    applied: list[str] = []
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table, columns in _EXPECTED.items():
            if table not in existing_tables:
                # Tabelle existiert noch nicht -> create_all legt sie vollständig an
                continue
            present = {col["name"] for col in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in present:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
                    applied.append(f"{table}.{name}")
    return applied
