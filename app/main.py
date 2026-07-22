"""Warenwirtschaftssystem — Zusammenbau der Anwendung.

Die eigentliche Arbeit liegt in:
  * `models`      — Datenmodell
  * `services`    — Fachlogik und gemeinsame Hilfsfunktionen
  * `maintenance` — Migrationen und Hintergrund-Aufgaben
  * `routers/`    — die Endpunkte, nach Themenbereich getrennt
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config
from .database import Base, engine
from .maintenance import (
    backfill_article_numbers,
    backfill_fulfillment,
    backfill_storage_locations,
    lifespan,
    migrate_legacy_sales,
)
from .migrations import run_migrations
from .routers import articles, dashboard, reports, sales, storage, system
from .web import BASE_DIR

# Schema anlegen bzw. fehlende Spalten/Indizes nachziehen, danach die
# einmaligen Datenmigrationen — alles idempotent.
Base.metadata.create_all(engine)
run_migrations(engine)
backfill_article_numbers()
backfill_storage_locations()
migrate_legacy_sales()
backfill_fulfillment()

app = FastAPI(title="Warenwirtschaftssystem", lifespan=lifespan)

# Statische Dateien & hochgeladene Bilder ausliefern
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/uploads", StaticFiles(directory=str(config.UPLOAD_DIR)), name="uploads")

# Reihenfolge zählt: /articles/new muss vor /articles/{id} registriert sein —
# innerhalb der Router ist die ursprüngliche Reihenfolge erhalten.
app.include_router(dashboard.router)
app.include_router(articles.router)
app.include_router(sales.router)
app.include_router(storage.router)
app.include_router(reports.router)
app.include_router(system.router)
