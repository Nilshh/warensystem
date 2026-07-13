#!/usr/bin/env bash
#
# Deploy-/Update-Skript für das Warenwirtschaftssystem.
# Auf dem Proxmox-LXC ausführen: ./deploy.sh
#
# Holt die neueste Version (git pull), baut den Docker-Container neu,
# startet ihn und prüft per Health-Check, ob die App läuft.
#
set -euo pipefail

# Immer im Verzeichnis des Skripts arbeiten
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
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
exit 1
