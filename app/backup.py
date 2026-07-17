"""Backup & Restore: Datenbank + hochgeladene Bilder als ZIP.

Backup nutzt die SQLite-Online-Backup-API für einen konsistenten Snapshot.
Restore ersetzt DB und Bilder — die offenen DB-Verbindungen werden vorher
geschlossen (engine.dispose()).
"""
from __future__ import annotations

import datetime
import io
import os
import pathlib
import sqlite3
import tempfile
import zipfile

from . import config
from .database import engine

DB_ARCNAME = "warensystem.db"


def create_backup() -> bytes:
    """Erzeugt ein ZIP mit konsistentem DB-Snapshot + allen Bildern."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # konsistenten DB-Snapshot über die Online-Backup-API ziehen
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            src = sqlite3.connect(str(config.DB_PATH))
            dst = sqlite3.connect(tmp_path)
            with dst:
                src.backup(dst)
            dst.close()
            src.close()
            zf.write(tmp_path, DB_ARCNAME)
        finally:
            os.unlink(tmp_path)

        # alle Bilder mitnehmen
        for p in sorted(config.UPLOAD_DIR.glob("*")):
            if p.is_file():
                zf.write(p, f"uploads/{p.name}")

    buf.seek(0)
    return buf.getvalue()


def write_backup_file(directory=None, keep: int | None = None) -> pathlib.Path:
    """Schreibt eine Sicherung als Datei und räumt alte auf.

    Gibt den Pfad der neuen Sicherung zurück. Wirft OSError, wenn das
    Verzeichnis nicht beschreibbar ist.
    """
    directory = pathlib.Path(directory or config.BACKUP_DIR)
    keep = config.KEEP_BACKUPS if keep is None else keep
    directory.mkdir(parents=True, exist_ok=True)

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"warensystem-auto-{stamp}.zip"
    path.write_bytes(create_backup())

    # Rotation: nur die letzten `keep` automatischen Sicherungen behalten
    if keep > 0:
        autos = sorted(
            directory.glob("warensystem-auto-*.zip"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in autos[keep:]:
            old.unlink(missing_ok=True)
    return path


def restore_backup(data: bytes) -> None:
    """Ersetzt DB + Bilder aus einem Backup-ZIP.

    Wirft ValueError bei ungültigem Archiv.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("Keine gültige ZIP-Datei.")

    with zf:
        names = zf.namelist()
        if DB_ARCNAME not in names:
            raise ValueError(f"Ungültiges Backup: {DB_ARCNAME} fehlt.")

        db_bytes = zf.read(DB_ARCNAME)
        # Bild-Einträge einsammeln (nur Basenamen, gegen Zip-Slip geschützt)
        image_entries = [
            n for n in names
            if n.startswith("uploads/") and not n.endswith("/") and os.path.basename(n)
        ]

        # offene DB-Verbindungen schließen, dann Datei ersetzen
        engine.dispose()
        with open(config.DB_PATH, "wb") as f:
            f.write(db_bytes)

        # vorhandene Bilder entfernen und aus dem Backup wiederherstellen
        for p in config.UPLOAD_DIR.glob("*"):
            if p.is_file():
                p.unlink()
        for name in image_entries:
            base = os.path.basename(name)
            with open(config.UPLOAD_DIR / base, "wb") as f:
                f.write(zf.read(name))
