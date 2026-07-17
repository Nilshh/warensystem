#!/usr/bin/env bash
#
# Deploy-/Update-Skript für das Warenwirtschaftssystem.
# Auf dem Proxmox-LXC ausführen: ./deploy.sh
#
# Ablauf: Backup -> neueste Version holen (git pull) -> Container neu bauen
# und starten -> Health-Check.
#
# Umgebungsvariablen:
#   PORT=8000            Port der App
#   BACKUP_DIR=./backups Ablage der Sicherungen
#   KEEP_BACKUPS=10      Wie viele Sicherungen aufgehoben werden (0 = alle)
#
set -euo pipefail

# Immer im Verzeichnis des Skripts arbeiten
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP_BACKUPS="${KEEP_BACKUPS:-10}"
COMPOSE="docker compose"

log()  { printf '\033[1;34m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
err()  { printf '\033[1;31m✖ %s\033[0m\n' "$*" >&2; }

# --- Voraussetzungen prüfen -------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  err "Docker ist nicht installiert. Bitte zuerst installieren:"
  err "  apt update && apt install -y docker.io docker-compose-plugin git"
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  # Fallback auf das ältere 'docker-compose'
  if command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
  else
    err "Docker-Compose-Plugin fehlt. Installieren mit: apt install -y docker-compose-plugin"
    exit 1
  fi
fi

# --- Backup VOR dem Deploy --------------------------------------------------
# Bevorzugt über die laufende App (/backup.zip): Das nutzt die SQLite-Online-
# Backup-API und liefert einen konsistenten Snapshot inkl. Bilder. Nur wenn die
# App nicht erreichbar ist, wird das Datenverzeichnis als tar gesichert.
if [ -d data ] && [ -n "$(ls -A data 2>/dev/null)" ]; then
  if ! mkdir -p "$BACKUP_DIR" 2>/dev/null; then
    err "Backup-Verzeichnis ${BACKUP_DIR} lässt sich nicht anlegen – Deploy abgebrochen."
    err "Schreibrechte prüfen oder anderes Ziel setzen: BACKUP_DIR=/pfad ./deploy.sh"
    exit 1
  fi
  STAMP="$(date +%Y%m%d-%H%M%S)"
  log "Erstelle Backup vor dem Deploy…"

  if curl -sf --max-time 120 "http://127.0.0.1:${PORT}/backup.zip" \
       -o "${BACKUP_DIR}/warensystem-deploy-${STAMP}.zip" 2>/dev/null; then
    ok "Backup: ${BACKUP_DIR}/warensystem-deploy-${STAMP}.zip ($(du -h "${BACKUP_DIR}/warensystem-deploy-${STAMP}.zip" | cut -f1))"
  else
    rm -f "${BACKUP_DIR}/warensystem-deploy-${STAMP}.zip"
    log "App nicht erreichbar – sichere stattdessen das Datenverzeichnis…"
    if tar -czf "${BACKUP_DIR}/warensystem-deploy-${STAMP}-data.tar.gz" data; then
      ok "Backup: ${BACKUP_DIR}/warensystem-deploy-${STAMP}-data.tar.gz ($(du -h "${BACKUP_DIR}/warensystem-deploy-${STAMP}-data.tar.gz" | cut -f1))"
    else
      err "Backup fehlgeschlagen – Deploy abgebrochen (keine Änderung an deinen Daten)."
      err "Prüfe Schreibrechte auf ${BACKUP_DIR} oder setze BACKUP_DIR=/pfad."
      exit 1
    fi
  fi

  # Alte Deploy-Sicherungen aufräumen (die automatischen Sicherungen der App
  # rotieren getrennt davon und werden hier nicht angefasst)
  if [ "$KEEP_BACKUPS" -gt 0 ]; then
    # shellcheck disable=SC2012
    ls -1t "${BACKUP_DIR}"/warensystem-deploy-* 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | while read -r old; do
      rm -f "$old"
    done
  fi
else
  log "Noch keine Daten vorhanden – Backup übersprungen (Erstinstallation)."
fi

# --- Neueste Version holen --------------------------------------------------
if [ -d .git ]; then
  log "Hole neueste Version (git pull)…"
  git pull --ff-only
else
  log "Kein Git-Repo – überspringe git pull."
fi

# --- Bauen & starten --------------------------------------------------------
log "Baue und starte Container…"
$COMPOSE up -d --build

# --- Aufräumen (alte, ungenutzte Images) ------------------------------------
log "Räume ungenutzte Docker-Images auf…"
docker image prune -f >/dev/null || true

# --- Health-Check -----------------------------------------------------------
log "Warte auf Start der App…"
URL="http://127.0.0.1:${PORT}/health"
for i in $(seq 1 30); do
  if curl -sf "$URL" >/dev/null 2>&1; then
    ok "App läuft."
    IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    ok "Erreichbar unter: http://${IP:-<container-ip>}:${PORT}"
    exit 0
  fi
  sleep 1
done

err "Health-Check fehlgeschlagen. Logs:"
$COMPOSE logs --tail=40
if [ -d "$BACKUP_DIR" ]; then
  err "Falls nötig: letztes Backup liegt in ${BACKUP_DIR} und kann über"
  err "das Dashboard (Datensicherung -> Backup einspielen) zurückgespielt werden."
fi
exit 1
