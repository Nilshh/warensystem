# 📦 Warenwirtschaftssystem

Schlankes Warenwirtschaftssystem, um den Überblick über eBay- und
Kleinanzeigen-Verkäufe zu behalten. Artikel anlegen & verwalten, Links zu den
Inseraten pflegen (parallel auf beiden Plattformen möglich), Status, Versandart,
Preise, Kosten, Gebühren und Gewinn erfassen — mit Dashboard und CSV-Export.

Läuft als **ein Docker-Container** (Python/FastAPI + SQLite), ideal für einen
LXC-Container im Proxmox-Cluster. Bedienung über die **Web-UI**.

## Funktionen

- Artikelverwaltung (Titel, Kategorie, Zustand, Beschreibung, Status, **Tags**)
- **Automatische Artikelnummer** beim Anlegen (Präfix per `.env`, z.B. `WA-00001`)
- **Bestand & Verkaufshistorie**: Ein Artikel kann mehrfach vorhanden sein
  (Stückzahl). Jeder Verkauf wird einzeln erfasst (Stückzahl, Käufer, Preis,
  Gebühren, Versand) und reduziert den Bestand — mit voller Historie je Artikel.
- **Verkäufe-Reiter**: eigene Übersicht aller Verkäufe mit Suche/Jahresfilter;
  Verkäufe lassen sich **nachträglich korrigieren oder löschen** — der Bestand
  wird dabei automatisch mitgeführt.
- **Geführter Verkauf-Workflow** und **automatische Archivierung** ausverkaufter
  Artikel nach einstellbarer Frist (`ARCHIVE_AFTER_DAYS`, Standard 7 Tage;
  Verkäufe zählen in der Statistik weiter)
- **Bilder-Upload** pro Artikel, inkl. **Hauptbild festlegen**
- Links + Angebots-Status für **eBay** und **Kleinanzeigen** (parallel)
- **Preise & Kosten**: Einkauf, Angebotspreis, Verkaufspreis, Versandart (Dropdown), Versandkosten, Gebühren
- **Automatische Gewinn- und Margenberechnung**
- **Käufer- & Versandabwicklung**: verkauft über, Käufer, Zahlungsart, Bestell-/Versanddatum, Sendungsverfolgung
- **Dashboard** mit **Jahresfilter**: Umsatz, Kosten, Gewinn, offene Artikel, gebundenes Kapital,
  Status-Übersicht und **Monats-Diagramm** (Umsatz & Gewinn)
- **Artikelliste** mit Suche, Status-/Tag-Filter und **sortierbaren Spalten**
- **Artikel duplizieren** (als Vorlage)
- **Lagerverwaltung**: Lagerplätze (Bereich/Regal/Fach) werden im **Lager-Reiter**
  angelegt/verwaltet und im Artikel nur per **Auswahl** genutzt. Übersicht aller
  Lagerorte, anklickbare Lagerfächer/Kisten (zeigt den Inhalt), **QR-Etikett je
  Lagerort** (Scan zeigt, was dort liegen soll). **Massenbearbeitung**: Lagerplatz
  für mehrere Artikel gemeinsam setzen. Beim Verkauf wird der Lagerplatz frei.
- **QR-Etikett** pro Artikel — druckbares Etikett, dessen QR-Code auf die
  Artikelseite verweist (Ziel-URL via `BASE_URL` in der `.env`)
- **Lieferschein/Packzettel** pro verkauftem Artikel — druckoptimiert (Browser → „Als PDF speichern")
- **Massen-Statusänderung**: mehrere Artikel auswählen und Status gemeinsam setzen
- **CSV-Export** (Excel-kompatibel): **Bestandsliste** (Artikel mit Bestand/Wert)
  und **Verkaufsliste** (jeder Verkauf eine Zeile, optional pro Jahr — für Buchhaltung/Steuer)
- **Import per eBay-Link** (Browse API): Titel, Preis, Zustand, Beschreibung und Bilder
  aus einem Inserat übernehmen — aktiv, sobald App-Keys hinterlegt sind
- eBay-Verkaufs-Sync im Code **vorbereitet** (siehe `app/ebay.py`), Kleinanzeigen bleibt manuell

Für den **Lieferschein** trägst du deine Absenderdaten in die `.env` ein
(`SELLER_NAME`, `SELLER_ADDRESS`, optional `SELLER_EMAIL`/`SELLER_PHONE`);
die Lieferadresse des Käufers wird pro Artikel erfasst.

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

Für spätere Updates gibt es `deploy.sh`:

```bash
./deploy.sh
```

Ablauf: **Backup erstellen** → neueste Version holen → Container neu bauen &
starten → alte Images aufräumen → Health-Check.

Das Backup wird vor jeder Änderung erstellt und landet in `./backups`
(konsistenter Snapshot über die laufende App; falls sie nicht erreichbar ist,
wird ersatzweise das Datenverzeichnis als `tar.gz` gesichert). Schlägt das
Backup fehl, bricht das Skript ab, ohne etwas zu verändern.

Anpassbar per Umgebungsvariablen:

```bash
PORT=8080 ./deploy.sh            # anderer Port
BACKUP_DIR=/mnt/nas ./deploy.sh  # Backups woanders ablegen
KEEP_BACKUPS=30 ./deploy.sh      # mehr Sicherungen aufheben (0 = alle behalten)
```

> Es gibt bewusst **kein Login** (nur im LAN gedacht). Wenn du es später von
> außen erreichbar machst, unbedingt einen Reverse-Proxy mit Authentifizierung
> davorsetzen.

## Backup

Zwei Wege:

- **Per Knopfdruck (Web-UI):** Auf dem Dashboard unter *Datensicherung* →
  **„Backup herunterladen"** lädt ein ZIP mit Datenbank + allen Bildern.
  **„Backup einspielen"** stellt einen früheren Stand wieder her (überschreibt
  die aktuellen Daten vollständig — mit Sicherheitsabfrage).
- **Serverseitig:** Es reicht, den Ordner `./data` zu sichern (DB + Bilder),
  z.B. per Proxmox-Backup des Containers oder einem `tar`/`rsync`-Job.

## eBay-Import aktivieren (per Link)

Für den Import per eBay-Link brauchst du einen kostenlosen
**eBay-Developer-Account** (developer.ebay.com). Der Account ist getrennt vom
normalen eBay-Konto — man registriert sich dort einmalig.

1. Unter **Application Keysets** ein **Production**-Keyset erzeugen.
2. **App ID (Client ID)** und **Cert ID (Client Secret)** in die `.env` eintragen:
   ```
   EBAY_CLIENT_ID=DeineAppId
   EBAY_CLIENT_SECRET=DeinCertId
   ```
3. Container neu starten (`./deploy.sh` oder `docker compose up -d`).

Danach erscheint im Formular „Neuer Artikel" oben eine Box **Aus eBay
importieren**: Link oder Artikelnummer einfügen → Titel, Preis, Zustand,
Beschreibung und Bilder werden übernommen (als Entwurf zum Prüfen).

Der Import nutzt nur einen **App-Token** (Client-Credentials) — kein
Nutzer-Login. Solange keine Keys gesetzt sind, ist die Box inaktiv.

Zum Testen kann per `.env` auf die Sandbox umgestellt werden
(`EBAY_ENV=sandbox` mit Sandbox-Keys).

## Verkaufs-Synchronisierung (später)

Die vollautomatische Übernahme verkaufter Artikel (Sell API) benötigt zusätzlich
einen **Nutzer-Refresh-Token** (`EBAY_REFRESH_TOKEN`) und die Implementierung von
`sync_orders()` in [`app/ebay.py`](app/ebay.py). Solange dieser Token fehlt,
bleibt die Sync-Schaltfläche inaktiv.

## Tests

Die Geschäftslogik ist durch automatisierte Tests abgesichert (Gewinn- und
Margenberechnung, Bestandsführung, Verkaufskorrekturen, Datenmigration,
Backup/Restore, Filter und Lagerverwaltung):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

Die Tests laufen gegen eine temporäre Datenbank und fassen deine Daten nicht an.

## Technik

- **Backend**: FastAPI, SQLAlchemy, SQLite
- **Frontend**: serverseitige Jinja2-Templates + etwas Vanilla-JS (kein Build-Schritt)
- **Container**: `python:3.12-slim`, Start via `uvicorn`
