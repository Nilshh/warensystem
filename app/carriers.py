"""Sendungsverfolgung bei Versanddienstleistern.

Aktuell ist **DHL** angebunden — über die offizielle „Shipment Tracking –
Unified"-API (developer.dhl.com, kostenloser API-Key, ~250 Abfragen/Tag).

Andere Anbieter sind bewusst offen gelassen:
  * **Hermes** bietet für Privatkunden keine öffentliche Tracking-API. Nur mit
    Geschäftskundenvertrag (ProfiPaketService) gäbe es eine Schnittstelle. Die
    internen Endpunkte der Webseite anzuzapfen wäre fragil und gegen die
    Nutzungsbedingungen — daher nicht umgesetzt.
  * **UPS** hätte eine offizielle API, benötigt aber ein Geschäftskonto.

Zum Ergänzen eines Anbieters genügt eine Funktion nach dem Muster von
`_track_dhl` plus ein Eintrag in `_CARRIERS`.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime

from . import config

# Normalisierte Zustände (unabhängig vom Anbieter)
UNTERWEGS = "unterwegs"
ZUGESTELLT = "zugestellt"
PROBLEM = "problem"
UNBEKANNT = "unbekannt"

STATUS_LABELS = {
    UNTERWEGS: "Unterwegs",
    ZUGESTELLT: "Zugestellt",
    PROBLEM: "Problem",
    UNBEKANNT: "Unbekannt",
}


class TrackingError(RuntimeError):
    """Abfrage beim Dienstleister fehlgeschlagen."""


@dataclass
class TrackingResult:
    status: str                      # einer der Zustände oben
    text: str = ""                   # Klartext des Dienstleisters
    delivered_at: datetime | None = None


# --- DHL --------------------------------------------------------------------
_DHL_URL = "https://api-eu.dhl.com/track/shipments"

# statusCode der DHL-API -> unser Zustand
_DHL_STATUS = {
    "delivered": ZUGESTELLT,
    "transit": UNTERWEGS,
    "pre-transit": UNTERWEGS,
    "failure": PROBLEM,
    "unknown": UNBEKANNT,
}


def _parse_dhl_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _track_dhl(number: str) -> TrackingResult:
    if not config.DHL_API_KEY:
        raise TrackingError("Kein DHL-API-Key hinterlegt.")

    url = f"{_DHL_URL}?{urllib.parse.urlencode({'trackingNumber': number, 'language': 'de'})}"
    req = urllib.request.Request(
        url, headers={"DHL-API-Key": config.DHL_API_KEY, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Nummer (noch) nicht im System — kein Fehler, nur nichts bekannt
            return TrackingResult(status=UNBEKANNT, text="Noch keine Daten bei DHL")
        if e.code == 429:
            raise TrackingError("DHL-Abfragelimit erreicht — später erneut versuchen.")
        if e.code == 401:
            raise TrackingError(
                "DHL lehnt den API-Key ab (401). Im Portal ist der 'API Key' "
                "bzw. 'Consumer Key' gemeint — nicht das Consumer Secret."
            )
        if e.code == 403:
            raise TrackingError(
                "DHL-API-Key ist nicht für 'Shipment Tracking – Unified' "
                "freigeschaltet (403). Abo/Freigabe der App im Portal prüfen."
            )
        raise TrackingError(f"DHL-Abfrage fehlgeschlagen ({e.code}).")
    except urllib.error.URLError as e:
        raise TrackingError(f"DHL nicht erreichbar: {e.reason}")

    shipments = data.get("shipments") or []
    if not shipments:
        return TrackingResult(status=UNBEKANNT, text="Keine Sendung gefunden")

    status = shipments[0].get("status") or {}
    code = (status.get("statusCode") or "").lower()
    zustand = _DHL_STATUS.get(code, UNBEKANNT)
    text = status.get("status") or status.get("description") or ""
    delivered_at = _parse_dhl_time(status.get("timestamp")) if zustand == ZUGESTELLT else None
    return TrackingResult(status=zustand, text=text.strip(), delivered_at=delivered_at)


# --- Anbieter-Erkennung -----------------------------------------------------
_CARRIERS = {
    "dhl": _track_dhl,
}


def detect(*hints: str) -> str | None:
    """Erkennt den Dienstleister aus Freitext (z.B. "DHL", "DHL Paket 2 kg")."""
    for hint in hints:
        haystack = (hint or "").lower()
        for name in _CARRIERS:
            if name in haystack:
                return name
    return None


def supports(*hints: str) -> bool:
    """Kann dieser Dienstleister automatisch abgefragt werden?"""
    return detect(*hints) is not None


def is_configured() -> bool:
    return config.tracking_configured()


def track(carrier: str, number: str) -> TrackingResult:
    """Fragt den Sendungsstatus ab. `carrier` ist ein erkannter Anbietername."""
    fn = _CARRIERS.get(carrier)
    if not fn:
        raise TrackingError(f"Dienstleister '{carrier}' wird nicht unterstützt.")
    if not (number or "").strip():
        raise TrackingError("Keine Sendungsnummer.")
    return fn(number.strip())
