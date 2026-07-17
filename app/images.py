"""Bildverarbeitung: verkleinern und Vorschaubilder erzeugen.

Hintergrund: Handyfotos sind schnell mehrere MB groß. Ungefiltert gespeichert
werden sie auch dann komplett übertragen, wenn die Liste nur ein 44-px-Vorschau-
bild zeigt — über VPN unbrauchbar, und jedes Backup wird unnötig groß.

Deshalb wird beim Upload:
  * das Bild auf MAX_SIZE (längste Kante) begrenzt,
  * ein Thumbnail für Listen/Galerie erzeugt,
  * die EXIF-Drehung angewendet (sonst liegen Fotos quer),
  * als JPEG gespeichert (Ausnahme: PNG mit Transparenz bleibt PNG).
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps

# Längste Kante des gespeicherten Bildes bzw. des Vorschaubildes
MAX_SIZE = 1600
THUMB_SIZE = 400
JPEG_QUALITY = 85

THUMB_PREFIX = "thumb_"


def thumb_name(filename: str) -> str:
    return f"{THUMB_PREFIX}{filename}"


def _has_alpha(img: Image.Image) -> bool:
    """Bild mit Transparenz? Dann darf es nicht nach JPEG konvertiert werden."""
    return img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )


def process_upload(data: bytes, dest_dir: Path, stem: str) -> str:
    """Speichert ein hochgeladenes Bild verkleinert + als Thumbnail.

    Gibt den Dateinamen des Hauptbildes zurück. Wirft OSError/ValueError,
    wenn die Daten kein lesbares Bild sind.
    """
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)      # Drehung aus den EXIF-Daten anwenden

    if _has_alpha(img):
        img = img.convert("RGBA")
        suffix, save_kw = ".png", {"optimize": True}
    else:
        img = img.convert("RGB")
        suffix, save_kw = ".jpg", {"quality": JPEG_QUALITY, "optimize": True,
                                   "progressive": True}

    filename = f"{stem}{suffix}"

    full = img.copy()
    full.thumbnail((MAX_SIZE, MAX_SIZE), Image.LANCZOS)   # nur verkleinern, nie hochskalieren
    full.save(dest_dir / filename, **save_kw)

    thumb = img.copy()
    thumb.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
    thumb.save(dest_dir / thumb_name(filename), **save_kw)

    return filename


def optimize_existing(dest_dir: Path, filename: str) -> bool:
    """Verkleinert ein bereits gespeichertes Bild an Ort und Stelle, falls zu groß.

    Das Format bleibt erhalten (der Dateiname steht in der Datenbank).
    Gibt True zurück, wenn das Bild verkleinert wurde.
    """
    src = dest_dir / filename
    if not src.exists():
        return False
    try:
        img = Image.open(src)
        if max(img.size) <= MAX_SIZE:
            return False                     # schon klein genug
        img = ImageOps.exif_transpose(img)
        save_kw = {}
        if not _has_alpha(img):
            img = img.convert("RGB")
            if src.suffix.lower() in (".jpg", ".jpeg"):
                save_kw = {"quality": JPEG_QUALITY, "optimize": True, "progressive": True}
        img.thumbnail((MAX_SIZE, MAX_SIZE), Image.LANCZOS)
        img.save(src, **save_kw)
        return True
    except Exception:
        return False


def ensure_thumb(dest_dir: Path, filename: str) -> bool:
    """Erzeugt ein fehlendes Thumbnail für ein bereits vorhandenes Bild.

    Gibt True zurück, wenn eines erzeugt wurde.
    """
    src = dest_dir / filename
    dst = dest_dir / thumb_name(filename)
    if dst.exists() or not src.exists():
        return False
    try:
        img = Image.open(src)
        img = ImageOps.exif_transpose(img)
        if not _has_alpha(img):
            img = img.convert("RGB")
        img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
        img.save(dst)
        return True
    except Exception:
        return False
