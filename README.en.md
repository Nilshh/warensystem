# 📦 Inventory Management System (Warenwirtschaftssystem)

*Language: [Deutsch](README.md) · **English***

A lean inventory system to keep track of your **eBay and Kleinanzeigen sales**:
manage items and stock, offer on both platforms in parallel, record and fulfil
sales, and keep an eye on profit and statistics.

Runs as **a single Docker container** (Python/FastAPI + SQLite), ideal for an LXC
container in a Proxmox cluster. Operated entirely through the **web UI**.

> The application UI is in **German**. This document translates the setup and
> concepts; the buttons and labels in the app remain German.

---

## Contents

- [Features](#features)
- [Quick start (local test)](#quick-start-local-test)
- [Fresh install on a Proxmox LXC](#fresh-install-on-a-proxmox-lxc)
- [Configuration (.env)](#configuration-env)
- [Updating / redeploying](#updating--redeploying)
- [Backup & restore](#backup--restore)
- [eBay import setup](#ebay-import-setup)
- [Shipment tracking (DHL) setup](#shipment-tracking-dhl-setup)
- [How it works: the workflow](#how-it-works-the-workflow)
- [Tests](#tests)
- [Architecture](#architecture)
- [Troubleshooting](#troubleshooting)

---

## Features

**Items & stock**
- Item management (title, category dropdown, condition, description, status, tags)
- **Stock per item** (quantity) — an item can exist multiple times
- **Images** per item: automatically resized on upload (max. 1600 px), rotated
  (EXIF) and stored with a thumbnail; a main image can be set
- **Automatic item number** (`WA-00001`, prefix configurable)
- **QR label** per item — the QR code opens the item page
- **Goods intake / split a lot**: distribute one purchase price across several
  items (weighted by expected sale value)

**Platforms**
- Links + listing status for **eBay** and **Kleinanzeigen** (in parallel)
- **Import from an eBay link** (single or several at once): title, price,
  condition, description, images and quantity
- Manually **refresh a single item from eBay**

**Selling & fulfilment**
- **Guided sale flow** per item (quantity, buyer, price, fees, shipping)
- **Sales history** per item; edit/delete sales later (stock is kept in sync)
- **Fulfilment flow** per sale: Sold → Paid → Shipped → Delivered → Completed,
  plus **Cancelled/Return** (rebooks the stock)
- **Automation**: shipping date → *Shipped*, DHL delivery → *Delivered*, after a
  grace period automatically *Completed*
- **Delivery note / packing slip** per sale (print-optimised)
- **Shipment tracking (DHL)**: automatic twice a day, stops on delivery

**Storage**
- Manage storage locations (area/shelf/bin) in the storage section
- In the item, choose from the list only; bulk assignment supported
- Location view shows its contents; **QR label per bin/box**

**Reporting & overview**
- **Dashboard**: revenue/cost/profit (year filter), monthly chart, stock, tied-up
  capital, task list (*to do*), stuck shipments
- **Reports**: slow movers, platform comparison, avg. time-to-sale per category
- **CSV export**: stock list and sales list (for bookkeeping/taxes)

**Operations**
- **Automatic daily backups** with rotation
- **One-click backup/restore** (DB + images as a ZIP)
- **Auto-migration**: new fields are added on start — updates never require
  resetting the database
- No login (designed for LAN use)

---

## Quick start (local test)

Requirement: Docker with the Compose plugin.

```bash
git clone https://github.com/Nilshh/warensystem.git
cd warensystem
cp .env.example .env        # adjust if needed
docker compose up -d --build
```

Open in your browser: <http://localhost:8000>

Data (SQLite DB + images) lives in `./data` and survives restarts.

---

## Fresh install on a Proxmox LXC

**1. Create an LXC container** (Debian/Ubuntu). Docker works best in a
   *privileged* container **or** with nesting enabled:
   in Proxmox under *Options → Features* set `nesting=1`.

**2. Install Docker + Git in the container:**
```bash
apt update && apt install -y docker.io docker-compose-plugin git
```

**3. Get and configure the project:**
```bash
cd /opt
git clone https://github.com/Nilshh/warensystem.git
cd warensystem
cp .env.example .env
nano .env            # sender details & optional keys (see below)
```

**4. Start:**
```bash
docker compose up -d --build
```

**5. Open** via the container's LAN IP: `http://<container-ip>:8000`

**6. (Optional) friendly hostname:** to reach it at e.g. `http://wa.home`, add a
`wa.home → <container-ip>` entry in your router/Pi-hole/DNS and set
`BASE_URL=http://wa.home` in `.env` (this is the address the QR codes point to).

> **No login:** the system is intentionally built without authentication (LAN
> only). If you expose it externally, put a reverse proxy with authentication in
> front of it.

---

## Configuration (.env)

All settings go through the `.env` file (a copy of `.env.example`). After
changes: `docker compose up -d` (a `restart` alone does **not** pick up new
variables).

| Variable | Default | Meaning |
|---|---|---|
| `EBAY_FEE_PERCENT` | `11.0` | Suggested fee rate in the sale form |
| `ARTICLE_NO_PREFIX` | `WA-` | Item number prefix (`WA-00001`) |
| `BASE_URL` | `http://wa.home` | Target of the QR codes (item/storage) |
| `ARCHIVE_AFTER_DAYS` | `7` | Archive sold-out items after X days (0 = off) |
| `AUTO_COMPLETE_DAYS` | `14` | Complete delivered sales after X days (0 = off) |
| `AUTO_BACKUP_HOURS` | `24` | Auto-backup interval in hours (0 = off) |
| `KEEP_BACKUPS` | `10` | Number of backups to keep |
| `BACKUP_DIR` | `/backups` | Backup location (inside the container) |
| **Delivery note** | | |
| `SELLER_NAME` | – | Your name (sender on the delivery note) |
| `SELLER_ADDRESS` | – | Address, multi-line via `\n` |
| `SELLER_EMAIL` / `SELLER_PHONE` | – | optional |
| **eBay import** | | |
| `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` | – | App keys (see below) |
| `EBAY_ENV` | `production` | `production` or `sandbox` |
| `EBAY_MARKETPLACE_ID` | `EBAY_DE` | Marketplace |
| `EBAY_REFRESH_TOKEN` | – | only for future sales sync |
| **Shipment tracking** | | |
| `DHL_API_KEY` | – | DHL tracking key (see below) |
| `TRACKING_INTERVAL_HOURS` | `12` | Poll interval (12 = twice daily, 0 = off) |
| `TRACKING_STUCK_DAYS` | `7` | when a shipment is flagged on the dashboard |
| `TRACKING_MAX_DAYS` | `60` | stop polling after this many days |

---

## Updating / redeploying

```bash
cd /opt/warensystem
./deploy.sh
```

Flow: **create a backup → pull the latest version → rebuild & restart the
container → prune old images → health check.**

If the backup fails, the script aborts without changing anything. Configurable
via environment variables:

```bash
PORT=8080 ./deploy.sh            # different port
BACKUP_DIR=/mnt/nas ./deploy.sh  # store backups elsewhere
KEEP_BACKUPS=30 ./deploy.sh      # keep more backups
```

---

## Backup & restore

Three layers, deliberately redundant:

1. **Automatic daily** — a background task backs up to `./backups`
   (interval/count via `AUTO_BACKUP_HOURS` / `KEEP_BACKUPS`).
2. **Before every deploy** — `deploy.sh` backs up before changing anything.
3. **One click** — Dashboard → *Datensicherung*: “Backup herunterladen”
   downloads a ZIP (DB + all images); “Backup einspielen” restores a previous
   state (overwrites everything, with a confirmation).

A backup is a ZIP with a consistent SQLite snapshot **plus all images**. On the
server it is also enough to back up the `./data` folder (e.g. via a Proxmox
container backup).

---

## eBay import setup

You need a free **eBay developer account** (developer.ebay.com; separate from
your normal eBay account).

1. Create a **Production** keyset under **Application Keysets**.
2. Put **App ID (Client ID)** and **Cert ID (Client Secret)** into `.env`:
   ```
   EBAY_CLIENT_ID=YourAppId
   EBAY_CLIENT_SECRET=YourCertId
   ```
3. For “Marketplace Account Deletion”, choose the **exemption**
   (“I do not persist eBay data”) — the app stores no personal eBay account data.
4. `docker compose up -d`

The “New item” form then shows an **import box** (link or item number, several at
once too). It uses only an app token (client credentials) — no user login.
Without keys the box stays inactive. **Kleinanzeigen** has no official API and
remains manual.

---

## Shipment tracking (DHL) setup

1. Create a free account at **developer.dhl.com** and subscribe to the
   **“Shipment Tracking – Unified”** API.
2. Put the API key into `.env`:
   ```
   DHL_API_KEY=YourKey
   ```
   (This is the **API Key / Consumer Key** in the portal — not the *Consumer
   Secret*. The tracking API needs no secret.)
3. `docker compose up -d`

Open shipments are then polled **twice a day** — only sales with a tracking
number, and only until “delivered”. Shipments in transit longer than
`TRACKING_STUCK_DAYS` are flagged on the dashboard.

**Hermes** has no public tracking API for private customers and stays manual.
Additional carriers can be added in [`app/carriers.py`](app/carriers.py).

---

## How it works: the workflow

1. **Create an item** — manually, via **eBay import**, or through **goods
   intake** (split a lot). Enter stock, prices, storage location.
2. **List it** — set status to *Angeboten* (offered), add eBay/Kleinanzeigen
   links. Optionally print a QR label.
3. **Record a sale** — on the item page click “✅ Verkauf erfassen”: quantity,
   buyer, price, shipping. Stock decreases automatically.
4. **Fulfil** — move the sale through the steps: **Paid → Shipped → Delivered →
   Completed**. Much of it is automatic (shipping date, DHL tracking, auto-
   complete); the rest via buttons. The **dashboard** shows what’s still open
   under *Zu erledigen* (to do).
5. Print the **delivery note**, keep an eye on **reports**.

---

## Tests

The business logic is covered by automated tests (profit, stock handling, sale
corrections, fulfilment flow, migration, backup/restore, shipment tracking,
image processing, and more):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

Tests run against a temporary database and never touch your data.

---

## Architecture

- **Backend**: FastAPI, SQLAlchemy, SQLite
- **Frontend**: server-side Jinja2 templates + a little vanilla JS (no build step)
- **Images/QR**: Pillow, qrcode
- **Container**: `python:3.12-slim`, started via `uvicorn`

```
app/
  main.py         wiring (schema, migrations, routers)
  models.py       data model (items, sales, storage, images)
  services.py     business logic & shared helpers
  maintenance.py  migrations & background tasks (backup, tracking, auto-complete)
  carriers.py     shipment tracking (DHL)
  ebay.py         eBay import (Browse API)
  images.py       image resizing & thumbnails
  backup.py       backup/restore
  routers/        endpoints: dashboard, articles, sales, storage, reports, system
  templates/      web UI
data/             runtime data (DB + images) — not in the repo
backups/          automatic backups — not in the repo
```

---

## Troubleshooting

**Container doesn’t see `.env` changes** → `docker compose up -d` (not
`restart`).

**View logs:**
```bash
docker compose logs -f warensystem
```

**Tracking says “DHL lehnt die Anfrage ab (401)”** → either the key is wrong
(check Consumer Key vs. Secret) or the app isn’t approved for “Shipment Tracking
– Unified”. Direct test:
```bash
curl -s -o /dev/null -w "HTTP %{http_code}\n" \
  -H "DHL-API-Key: $DHL_API_KEY" \
  "https://api-eu.dhl.com/track/shipments?trackingNumber=00340434292135100186"
```

**App not reachable over VPN** → the VPN must route you into the home network
(allow local network access), otherwise requests bypass the LAN.
