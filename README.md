# 📦 Warenwirtschaftssystem

Schlankes Warenwirtschaftssystem, um den Überblick über eBay- und
Kleinanzeigen-Verkäufe zu behalten. Artikel anlegen & verwalten, Links zu den
Inseraten pflegen (parallel auf beiden Plattformen möglich), Status, Versandart,
Preise, Kosten, Gebühren und Gewinn erfassen — mit Dashboard und CSV-Export.

Läuft als **ein Docker-Container** (Python/FastAPI + SQLite), ideal für einen
LXC-Container im Proxmox-Cluster. Bedienung über die **Web-UI**.

## Funktionen

- Artikelverwaltung (Titel, Kategorie, Zustand, Beschreibung, Status, **Tags**)
- **Bilder-Upload** pro Artikel, inkl. **Hauptbild festlegen**
- Links + Angebots-Status für **eBay** und **Kleinanzeigen** (parallel)
- **Preise & Kosten**: Einkauf, Angebotspreis, Verkaufspreis, Versandart (Dropdown), Versandkosten, Gebühren
- **Automatische Gewinn- und Margenberechnung**
- **Käufer- & Versandabwicklung**: verkauft über, Käufer, Zahlungsart, Bestell-/Versanddatum, Sendungsverfolgung
- **Dashboard** mit **Jahresfilter**: Umsatz, Kosten, Gewinn, offene Artikel, gebundenes Kapital,
  Status-Übersicht und **Monats-Diagramm** (Umsatz & Gewinn)
- **Artikelliste** mit Suche, Status-/Tag-Filter und **sortierbaren Spalten**
- **Artikel duplizieren** (als Vorlage)
- **CSV-Export** (Excel-kompatibel, optional pro Jahr — für Buchhaltung/Steuer)
- eBay-API im Code **vorbereitet** (siehe `app/ebay.py`), Kleinanzeigen bleibt manuell

Secrets/Konfiguration liegen in einer nicht eingecheckten `.env` (siehe `.env.example`).
Das Datenbankschema wird beim Start automatisch um neue Felder ergänzt
(leichtgewichtige Migration in `app/migrations.py`) — ein Update erfordert kein
Zurücksetzen der Datenbank.

## Schnellstart (lokal zum Testen)

```bash
docker compose up --build
```

Dann im Browser: <http://localhost:8000>

Die Daten (SQLite-DB + hochgeladene Bilder) liegen im Ordner `./data` und bleiben
über Neustarts hinweg erhalten.

## Betrieb im Proxmox LXC-Container

1. **LXC-Container** anlegen (Debian/Ubuntu, Docker läuft am besten in einem
   *privilegierten* Container oder mit passenden Nesting-Optionen:
   in Proxmox unter *Options → Features* `nesting=1` aktivieren).
2. Im Container Docker + Compose-Plugin installieren:
   ```bash
   apt update && apt install -y docker.io docker-compose-plugin git
   ```
3. Dieses Repo klonen, `.env` anlegen und starten:
   ```bash
   git clone <repo-url> warensystem && cd warensystem
   cp .env.example .env       # optional anpassen (eBay-Gebühr, später API-Zugang)
   docker compose up -d --build
   ```
4. Aufruf über die LAN-IP des Containers: `http://<container-ip>:8000`

### Updaten / neu deployen

Für spätere Updates gibt es `deploy.sh` — holt die neueste Version, baut den
Container neu, räumt alte Images auf und prüft per Health-Check:

```bash
./deploy.sh
```

> Es gibt bewusst **kein Login** (nur im LAN gedacht). Wenn du es später von
> außen erreichbar machst, unbedingt einen Reverse-Proxy mit Authentifizierung
> davorsetzen.

## Backup

Es reicht, den Ordner `./data` zu sichern (enthält DB + Bilder), z.B. per
Proxmox-Backup des Containers oder einem `tar`/`rsync`-Job.

## eBay-API später aktivieren

Das Datenmodell speichert bereits `ebay_item_id` und `ebay_url` pro Artikel.
Sobald du einen eBay-Developer-Account hast:

1. Zugangsdaten in `docker-compose.yml` unter `environment` eintragen
   (`EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `EBAY_REFRESH_TOKEN`).
2. `sync_orders()` in [`app/ebay.py`](app/ebay.py) implementieren
   (eBay Sell Fulfillment API via OAuth).

Solange keine Zugangsdaten gesetzt sind, ist die Sync-Funktion in der UI inaktiv.

## Technik

- **Backend**: FastAPI, SQLAlchemy, SQLite
- **Frontend**: serverseitige Jinja2-Templates + etwas Vanilla-JS (kein Build-Schritt)
- **Container**: `python:3.12-slim`, Start via `uvicorn`
