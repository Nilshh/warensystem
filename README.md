# 📦 Warenwirtschaftssystem

*Sprache: **Deutsch** · [English](README.en.md)*

Schlankes Warenwirtschaftssystem, um den Überblick über **eBay- und
Kleinanzeigen-Verkäufe** zu behalten: Artikel & Bestand verwalten, parallel auf
beiden Plattformen anbieten, Verkäufe erfassen und abwickeln, Gewinn und
Statistik im Blick behalten.

Läuft als **ein Docker-Container** (Python/FastAPI + SQLite), ideal für einen
LXC-Container im Proxmox-Cluster. Bedienung komplett über die **Web-UI**.

---

## Inhalt

- [Funktionen](#funktionen)
- [Schnellstart (lokal testen)](#schnellstart-lokal-testen)
- [Neuinstallation auf einem Proxmox LXC](#neuinstallation-auf-einem-proxmox-lxc)
- [Konfiguration (.env)](#konfiguration-env)
- [Updaten / neu deployen](#updaten--neu-deployen)
- [Backup & Wiederherstellung](#backup--wiederherstellung)
- [eBay-Import einrichten](#ebay-import-einrichten)
- [Sendungsverfolgung (DHL) einrichten](#sendungsverfolgung-dhl-einrichten)
- [Bedienung: der Ablauf](#bedienung-der-ablauf)
- [Tests](#tests)
- [Technik & Aufbau](#technik--aufbau)
- [Fehlerbehebung](#fehlerbehebung)

---

## Funktionen

**Artikel & Bestand**
- Artikelverwaltung (Titel, Kategorie als Dropdown, Zustand, Beschreibung, Status, Tags)
- **Bestand pro Artikel** (Stückzahl) — ein Artikel kann mehrfach vorhanden sein
- **Bilder** pro Artikel: beim Upload automatisch verkleinert (max. 1600 px),
  gedreht (EXIF) und als Vorschaubild abgelegt; Hauptbild festlegbar
- **Automatische Artikelnummer** (`WA-00001`, Präfix einstellbar)
- **QR-Etikett** pro Artikel — der QR-Code führt zur Artikelseite
- **Wareneingang / Konvolut aufteilen**: Gesamtpreis auf mehrere Artikel
  verteilen (anteilig nach erwartetem Verkaufswert)

**Plattformen**
- Links + Angebots-Status für **eBay** und **Kleinanzeigen** (parallel)
- **Import per eBay-Link** (einzeln und mehrere auf einmal): Titel, Preis,
  Zustand, Beschreibung, Bilder und Stückzahl übernehmen
- Einzelnen Artikel manuell **von eBay aktualisieren**

**Verkauf & Abwicklung**
- **Geführter Verkaufsprozess** je Artikel (Stückzahl, Käufer, Preis, Gebühren, Versand)
- **Verkaufshistorie** je Artikel; Verkäufe nachträglich korrigieren/löschen
  (Bestand wird automatisch mitgeführt)
- **Abwicklungs-Ablauf** je Verkauf: Verkauft → Bezahlt → Versendet → Zugestellt
  → Abgeschlossen, plus **Storniert/Retoure** (bucht Bestand zurück)
- **Automatik**: Versanddatum → *Versendet*, DHL-Zustellung → *Zugestellt*,
  nach Frist automatisch *Abgeschlossen*
- **Lieferschein/Packzettel** je Verkauf (druckoptimiert)
- **Sendungsverfolgung (DHL)**: 2× täglich automatisch, Stopp bei Zustellung

**Lager**
- Lagerplätze (Bereich/Regal/Fach) im Lager-Bereich anlegen/verwalten
- Im Artikel nur per Auswahl nutzen; Massenzuweisung möglich
- Lagerort-Ansicht zeigt den Inhalt; **QR-Etikett je Lagerfach/Kiste**

**Auswertung & Übersicht**
- **Dashboard**: Umsatz/Kosten/Gewinn (Jahresfilter), Monats-Diagramm, Bestand,
  gebundenes Kapital, Aufgabenliste (*zu erledigen*), hängende Sendungen
- **Auswertung**: Ladenhüter, Plattform-Vergleich, Ø Verkaufsdauer je Kategorie
- **CSV-Export**: Bestandsliste und Verkaufsliste (für Buchhaltung/Steuer)

**Betrieb**
- **Automatische tägliche Sicherungen** mit Rotation
- **Backup/Restore per Knopfdruck** (DB + Bilder als ZIP)
- **Auto-Migration**: neue Felder werden beim Start ergänzt — Updates erfordern
  kein Zurücksetzen der Datenbank
- Kein Login (für den LAN-Betrieb gedacht)

---

## Schnellstart (lokal testen)

Voraussetzung: Docker mit Compose-Plugin.

```bash
git clone https://github.com/Nilshh/warensystem.git
cd warensystem
cp .env.example .env        # bei Bedarf anpassen
docker compose up -d --build
```

Aufruf im Browser: <http://localhost:8000>

Die Daten (SQLite-DB + Bilder) liegen im Ordner `./data` und bleiben über
Neustarts erhalten.

---

## Neuinstallation auf einem Proxmox LXC

**1. LXC-Container anlegen** (Debian/Ubuntu). Docker läuft am besten in einem
   *privilegierten* Container **oder** mit aktiviertem Nesting:
   in Proxmox unter *Options → Features* `nesting=1` setzen.

**2. Im Container Docker + Git installieren:**
```bash
apt update && apt install -y docker.io docker-compose-plugin git
```

**3. Projekt holen und einrichten:**
```bash
cd /opt
git clone https://github.com/Nilshh/warensystem.git
cd warensystem
cp .env.example .env
nano .env            # Absenderdaten & optionale Keys eintragen (siehe unten)
```

**4. Starten:**
```bash
docker compose up -d --build
```

**5. Aufruf** über die LAN-IP des Containers: `http://<container-ip>:8000`

**6. (Optional) Netter Name im Netzwerk:** Damit du das System unter z. B.
`http://wa.home` erreichst, trägst du in deinem Router/Pi-hole/DNS einen Eintrag
`wa.home → <container-ip>` ein und setzt in der `.env` `BASE_URL=http://wa.home`
(das ist die Adresse, auf die die QR-Codes zeigen).

> **Kein Login:** Das System ist bewusst ohne Anmeldung gebaut (nur LAN). Wenn du
> es von außen erreichbar machst, setz unbedingt einen Reverse-Proxy mit
> Authentifizierung davor.

---

## Konfiguration (.env)

Alle Einstellungen laufen über die Datei `.env` (Kopie von `.env.example`).
Nach Änderungen: `docker compose up -d` (ein `restart` allein zieht neue
Variablen **nicht**).

| Variable | Standard | Bedeutung |
|---|---|---|
| `EBAY_FEE_PERCENT` | `11.0` | Vorgeschlagener Gebührensatz im Verkaufsformular |
| `ARTICLE_NO_PREFIX` | `WA-` | Präfix der Artikelnummer (`WA-00001`) |
| `BASE_URL` | `http://wa.home` | Ziel der QR-Codes (Artikel/Lager) |
| `ARCHIVE_AFTER_DAYS` | `7` | Ausverkaufte Artikel nach X Tagen archivieren (0 = aus) |
| `AUTO_COMPLETE_DAYS` | `14` | Zugestellte Verkäufe nach X Tagen abschließen (0 = aus) |
| `AUTO_BACKUP_HOURS` | `24` | Intervall der Auto-Sicherung in Stunden (0 = aus) |
| `KEEP_BACKUPS` | `10` | Anzahl aufbewahrter Sicherungen |
| `BACKUP_DIR` | `/backups` | Ablage der Sicherungen (im Container) |
| **Lieferschein** | | |
| `SELLER_NAME` | – | Dein Name (Absender auf dem Lieferschein) |
| `SELLER_ADDRESS` | – | Adresse, mehrzeilig mit `\n` |
| `SELLER_EMAIL` / `SELLER_PHONE` | – | optional |
| **eBay-Import** | | |
| `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` | – | App-Keys (siehe unten) |
| `EBAY_ENV` | `production` | `production` oder `sandbox` |
| `EBAY_MARKETPLACE_ID` | `EBAY_DE` | Marktplatz |
| `EBAY_REFRESH_TOKEN` | – | nur für spätere Verkaufs-Sync |
| **Sendungsverfolgung** | | |
| `DHL_API_KEY` | – | DHL-Tracking-Key (siehe unten) |
| `TRACKING_INTERVAL_HOURS` | `12` | Abfrage-Intervall (12 = 2× täglich, 0 = aus) |
| `TRACKING_STUCK_DAYS` | `7` | ab wann eine Sendung im Dashboard gemeldet wird |
| `TRACKING_MAX_DAYS` | `60` | danach nicht mehr abfragen |

---

## Updaten / neu deployen

```bash
cd /opt/warensystem
./deploy.sh
```

Ablauf: **Backup erstellen → neueste Version holen (`git pull`) → Container neu
bauen & starten → alte Images aufräumen → Health-Check.**

Schlägt das Backup fehl, bricht das Skript ab, ohne etwas zu verändern.
Anpassbar per Umgebungsvariablen:

```bash
PORT=8080 ./deploy.sh            # anderer Port
BACKUP_DIR=/mnt/nas ./deploy.sh  # Backups woanders ablegen
KEEP_BACKUPS=30 ./deploy.sh      # mehr Sicherungen aufheben
```

---

## Backup & Wiederherstellung

Es gibt drei Ebenen — bewusst mehrfach abgesichert:

1. **Automatisch täglich** — ein Hintergrund-Task sichert nach `./backups`
   (Intervall/Anzahl über `AUTO_BACKUP_HOURS` / `KEEP_BACKUPS`).
2. **Vor jedem Deploy** — `deploy.sh` sichert automatisch, bevor es etwas ändert.
3. **Per Knopfdruck** — Dashboard → *Datensicherung*: „Backup herunterladen"
   lädt ein ZIP (DB + alle Bilder); „Backup einspielen" stellt einen früheren
   Stand wieder her (überschreibt alles, mit Sicherheitsabfrage).

Ein Backup ist ein ZIP mit konsistentem SQLite-Snapshot **plus allen Bildern**.
Serverseitig reicht zusätzlich, den Ordner `./data` zu sichern (z. B. per
Proxmox-Backup des Containers).

---

## eBay-Import einrichten

Für den Import per Link brauchst du einen kostenlosen **eBay-Developer-Account**
(developer.ebay.com; getrennt vom normalen eBay-Konto).

1. Unter **Application Keysets** ein **Production**-Keyset erzeugen.
2. **App ID (Client ID)** und **Cert ID (Client Secret)** in die `.env` eintragen:
   ```
   EBAY_CLIENT_ID=DeineAppId
   EBAY_CLIENT_SECRET=DeinCertId
   ```
3. Bei „Marketplace Account Deletion" die **Ausnahme** wählen
   („I do not persist eBay data") — die App speichert keine personenbezogenen
   eBay-Kontodaten.
4. `docker compose up -d`

Danach erscheint im Formular „Neuer Artikel" die Box **Aus eBay importieren**
(Link oder Artikelnummer, auch mehrere auf einmal). Genutzt wird nur ein
App-Token (Client-Credentials) — kein Nutzer-Login. Ohne Keys bleibt die Box
inaktiv. **Kleinanzeigen** hat keine offizielle API und bleibt manuell.

---

## Sendungsverfolgung (DHL) einrichten

1. Kostenlosen Account auf **developer.dhl.com** anlegen und die API
   **„Shipment Tracking – Unified"** abonnieren.
2. Den API-Key in die `.env` eintragen:
   ```
   DHL_API_KEY=DeinKey
   ```
   (Im Portal ist der **API Key / Consumer Key** gemeint — nicht das *Consumer
   Secret*. Die Tracking-API braucht kein Secret.)
3. `docker compose up -d`

Danach wird der Status offener Sendungen **2× täglich** automatisch abgefragt —
nur für Verkäufe mit Sendungsnummer, und nur bis „zugestellt". Sendungen, die
länger als `TRACKING_STUCK_DAYS` unterwegs sind, meldet das Dashboard.

**Hermes** hat keine öffentliche Tracking-API für Privatkunden und bleibt
manuell. Weitere Anbieter lassen sich in [`app/carriers.py`](app/carriers.py)
ergänzen.

---

## Bedienung: der Ablauf

1. **Artikel anlegen** — manuell, per **eBay-Import** oder über den
   **Wareneingang** (Konvolut aufteilen). Bestand, Preise, Lagerplatz eintragen.
2. **Anbieten** — Status auf *Angeboten*, Links/Häkchen für eBay/Kleinanzeigen
   setzen. Optional QR-Etikett drucken.
3. **Verkauf erfassen** — auf der Artikelseite „✅ Verkauf erfassen": Stückzahl,
   Käufer, Preis, Versand. Der Bestand sinkt automatisch.
4. **Abwickeln** — den Verkauf durch die Schritte führen:
   **Bezahlt → Versendet → Zugestellt → Abgeschlossen**. Vieles passiert
   automatisch (Versanddatum, DHL-Tracking, Auto-Abschluss); den Rest per Knopf.
   Das **Dashboard** zeigt unter *Zu erledigen*, was noch offen ist.
5. **Lieferschein** drucken, **Auswertung** im Blick behalten.

---

## Tests

Die Geschäftslogik ist durch automatisierte Tests abgesichert (Gewinn,
Bestandsführung, Verkaufskorrekturen, Abwicklungs-Ablauf, Migration,
Backup/Restore, Sendungsverfolgung, Bildverarbeitung u. v. m.):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

Die Tests laufen gegen eine temporäre Datenbank und fassen deine Daten nicht an.

---

## Technik & Aufbau

- **Backend**: FastAPI, SQLAlchemy, SQLite
- **Frontend**: serverseitige Jinja2-Templates + etwas Vanilla-JS (kein Build-Schritt)
- **Bilder/QR**: Pillow, qrcode
- **Container**: `python:3.12-slim`, Start via `uvicorn`

```
app/
  main.py         Zusammenbau (Schema, Migrationen, Router)
  models.py       Datenmodell (Artikel, Verkäufe, Lager, Bilder)
  services.py     Fachlogik & gemeinsame Hilfsfunktionen
  maintenance.py  Migrationen & Hintergrund-Aufgaben (Backup, Tracking, Auto-Abschluss)
  carriers.py     Sendungsverfolgung (DHL)
  ebay.py         eBay-Import (Browse API)
  images.py       Bildverkleinerung & Thumbnails
  backup.py       Sicherung/Wiederherstellung
  routers/        Endpunkte: dashboard, articles, sales, storage, reports, system
  templates/      Web-UI
data/             Laufzeitdaten (DB + Bilder) — nicht im Repo
backups/          automatische Sicherungen — nicht im Repo
```

---

## Fehlerbehebung

**Container sieht `.env`-Änderungen nicht** → `docker compose up -d` (nicht
`restart`).

**Logs ansehen:**
```bash
docker compose logs -f warensystem
```

**Sendungsverfolgung meldet „DHL lehnt die Anfrage ab (401)"** → entweder ist der
Key falsch (Consumer Key statt Secret prüfen) oder die App ist für „Shipment
Tracking – Unified" noch nicht freigegeben. Direkter Test:
```bash
curl -s -o /dev/null -w "HTTP %{http_code}\n" \
  -H "DHL-API-Key: $DHL_API_KEY" \
  "https://api-eu.dhl.com/track/shipments?trackingNumber=00340434292135100186"
```

**App nicht erreichbar über VPN** → der VPN muss dich ins Heimnetz bringen
(lokales Netz zulassen), sonst gehen die Anfragen am LAN vorbei.
