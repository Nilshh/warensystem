"""Bildverarbeitung: Verkleinern, Thumbnails, EXIF-Drehung."""
import io

from PIL import Image

from app import images
from app.models import ArticleImage


def _foto(w=4000, h=3000, mode="RGB", fmt="JPEG") -> bytes:
    """Erzeugt ein Testbild in typischer Handyfoto-Größe."""
    img = Image.new(mode, (w, h), (200, 100, 50) if mode == "RGB" else (200, 100, 50, 255))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# --- Verkleinern ------------------------------------------------------------
def test_grosses_foto_wird_verkleinert(tmp_path):
    name = images.process_upload(_foto(), tmp_path, "x")
    with Image.open(tmp_path / name) as img:
        assert max(img.size) == images.MAX_SIZE


def test_kleines_bild_wird_nicht_hochskaliert(tmp_path):
    name = images.process_upload(_foto(300, 200), tmp_path, "x")
    with Image.open(tmp_path / name) as img:
        assert img.size == (300, 200)


def test_thumbnail_wird_erzeugt(tmp_path):
    name = images.process_upload(_foto(), tmp_path, "x")
    thumb = tmp_path / images.thumb_name(name)
    assert thumb.exists()
    with Image.open(thumb) as img:
        assert max(img.size) == images.THUMB_SIZE


def test_dateigroesse_sinkt_deutlich(tmp_path):
    original = _foto()
    name = images.process_upload(original, tmp_path, "x")
    gespeichert = (tmp_path / name).stat().st_size
    thumb = (tmp_path / images.thumb_name(name)).stat().st_size
    # Das Vorschaubild muss um Größenordnungen kleiner sein als das Original
    assert gespeichert < len(original)
    assert thumb < gespeichert / 5


# --- Formate ----------------------------------------------------------------
def test_transparenz_bleibt_als_png(tmp_path):
    name = images.process_upload(_foto(800, 600, mode="RGBA", fmt="PNG"), tmp_path, "x")
    assert name.endswith(".png")
    with Image.open(tmp_path / name) as img:
        assert img.mode == "RGBA"


def test_foto_ohne_transparenz_wird_jpeg(tmp_path):
    assert images.process_upload(_foto(800, 600, fmt="PNG"), tmp_path, "x").endswith(".jpg")


def test_kaputte_datei_wirft_fehler(tmp_path):
    import pytest
    with pytest.raises(Exception):
        images.process_upload(b"kein bild", tmp_path, "x")


# --- EXIF-Drehung -----------------------------------------------------------
def test_exif_drehung_wird_angewendet(tmp_path):
    img = Image.new("RGB", (800, 400), "red")
    exif = img.getexif()
    exif[274] = 6                      # Orientation: 90° gedreht
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)

    name = images.process_upload(buf.getvalue(), tmp_path, "x")
    with Image.open(tmp_path / name) as out:
        assert out.size == (400, 800)  # Hoch- statt Querformat


# --- Bestandsbilder nachziehen ---------------------------------------------
def test_vorhandenes_grosses_bild_wird_verkleinert(tmp_path):
    (tmp_path / "alt.jpg").write_bytes(_foto())
    assert images.optimize_existing(tmp_path, "alt.jpg") is True
    with Image.open(tmp_path / "alt.jpg") as img:
        assert max(img.size) == images.MAX_SIZE
    # zweiter Lauf ändert nichts mehr
    assert images.optimize_existing(tmp_path, "alt.jpg") is False


def test_fehlendes_thumbnail_wird_nachgebaut(tmp_path):
    (tmp_path / "alt.jpg").write_bytes(_foto(800, 600))
    assert images.ensure_thumb(tmp_path, "alt.jpg") is True
    assert (tmp_path / images.thumb_name("alt.jpg")).exists()
    assert images.ensure_thumb(tmp_path, "alt.jpg") is False   # idempotent


# --- Upload über die App ----------------------------------------------------
def test_upload_legt_bild_und_thumbnail_an(client, db, make_article):
    from app import config
    a = make_article(title="Mit Bild")
    client.post(f"/articles/{a.id}/images",
                files={"file": ("foto.jpg", io.BytesIO(_foto()), "image/jpeg")})
    db.refresh(a)
    img = a.images[0]
    assert (config.UPLOAD_DIR / img.filename).exists()
    assert (config.UPLOAD_DIR / img.thumb).exists()
    with Image.open(config.UPLOAD_DIR / img.filename) as stored:
        assert max(stored.size) == images.MAX_SIZE


def test_liste_zeigt_thumbnail_statt_original(client, db, make_article):
    a = make_article(title="Mit Bild")
    client.post(f"/articles/{a.id}/images",
                files={"file": ("foto.jpg", io.BytesIO(_foto(800, 600)), "image/jpeg")})
    db.refresh(a)
    html = client.get("/articles").text
    assert f"/uploads/{a.images[0].thumb}" in html


def test_bild_loeschen_entfernt_auch_thumbnail(client, db, make_article):
    from app import config
    a = make_article(title="Mit Bild")
    client.post(f"/articles/{a.id}/images",
                files={"file": ("foto.jpg", io.BytesIO(_foto(800, 600)), "image/jpeg")})
    db.refresh(a)
    img = a.images[0]
    pfad, thumb = config.UPLOAD_DIR / img.filename, config.UPLOAD_DIR / img.thumb

    client.post(f"/articles/{a.id}/images/{img.id}/delete")
    assert not pfad.exists() and not thumb.exists()


def test_upload_lehnt_unbrauchbare_datei_ab(client, make_article):
    a = make_article(title="Ohne Bild")
    r = client.post(f"/articles/{a.id}/images",
                    files={"file": ("x.jpg", io.BytesIO(b"kein bild"), "image/jpeg")})
    assert r.status_code == 400
