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
    "sales": {
        "tracking_status": "VARCHAR(20) DEFAULT ''",
        "tracking_status_text": "VARCHAR(200) DEFAULT ''",
        "tracking_checked_at": "DATETIME",
        "tracking_delivered_at": "DATETIME",
    },
}


# Indizes auf häufig gefilterte/sortierte Spalten.
# create_all legt Indizes nur bei NEUEN Tabellen an — für bestehende Datenbanken
# müssen sie hier ergänzt werden.
_INDEXES = {
    "ix_articles_status": "articles(status)",
    "ix_articles_category": "articles(category)",
    "ix_articles_quantity": "articles(quantity)",
    "ix_articles_storage": "articles(storage_area, storage_shelf, storage_bin)",
    "ix_sales_sold_at": "sales(sold_at)",
}


def run_migrations(engine: Engine) -> list[str]:
    """Ergänzt fehlende Spalten und Indizes.

    Gibt eine Liste der durchgeführten Änderungen zurück.
    """
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

        for name, target in _INDEXES.items():
            table = target.split("(", 1)[0]
            if table in existing_tables:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {target}"))
    return applied
