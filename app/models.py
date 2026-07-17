"""Datenmodell."""
from datetime import datetime, timezone

from sqlalchemy import String, Float, DateTime, Text, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .images import thumb_name

# Lebenszyklus eines Artikels
STATUSES = ["Entwurf", "Angeboten", "Reserviert", "Verkauft", "Archiviert"]

# Gängige Zustände (frei erweiterbar im Formular)
CONDITIONS = ["Neu", "Neuwertig", "Gebraucht", "Defekt", "Ersatzteil"]

# Versandarten mit hinterlegten Standard-Versandkosten (€).
# Bei Auswahl wird der Kostenwert automatisch vorgeschlagen (überschreibbar).
# Preise bei Bedarf hier anpassen.
SHIPPING_OPTIONS = [
    # DHL (Online-Frankierung) — max. 2 kg bei Päckchen, sonst laut Gewichtsklasse
    {"label": "DHL Päckchen S (2 kg, 35×25×10)", "cost": 4.19},
    {"label": "DHL Päckchen M (2 kg, 60×30×15)", "cost": 5.19},
    {"label": "DHL Paket 2 kg (60×30×15, mit Tracking)", "cost": 6.19},
    {"label": "DHL Paket 5 kg (120×60×60)", "cost": 7.69},
    {"label": "DHL Paket 10 kg (120×60×60)", "cost": 10.49},
    {"label": "DHL Paket 20 kg (120×60×60)", "cost": 18.99},
    {"label": "DHL Paket 31,5 kg (120×60×60)", "cost": 23.99},
    # weitere Anbieter
    {"label": "Hermes", "cost": 4.50},
    {"label": "UPS", "cost": 6.99},
    {"label": "Selbstabholung", "cost": 0.00},
    {"label": "Sonstiges", "cost": 0.00},
]
SHIPPING_METHODS = [o["label"] for o in SHIPPING_OPTIONS]

# Wer trägt die Versandkosten (Standard: Käufer)
SHIPPING_PAYERS = ["Käufer", "Verkäufer"]

# Verkaufsplattform (für verkaufte Artikel)
SALE_PLATFORMS = ["eBay", "Kleinanzeigen", "Sonstige"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_no: Mapped[str] = mapped_column(String(20), default="", index=True)  # interne Artikelnummer
    title: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(default=1)  # verfügbarer Bestand (Stück)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(100), default="")
    condition: Mapped[str] = mapped_column(String(50), default="")
    status: Mapped[str] = mapped_column(String(30), default="Entwurf")

    # Preise & Kosten
    purchase_cost: Mapped[float] = mapped_column(Float, default=0.0)   # Einkaufskosten
    listing_price: Mapped[float] = mapped_column(Float, default=0.0)   # Angebotspreis je Stück
    # Vorbelegung für neue Verkäufe (der Verkauf speichert seine eigenen Werte)
    shipping_method: Mapped[str] = mapped_column(String(100), default="")
    shipping_cost: Mapped[float] = mapped_column(Float, default=0.0)
    shipping_payer: Mapped[str] = mapped_column(String(20), default="Käufer")

    # Plattform-Links (parallel möglich)
    ebay_url: Mapped[str] = mapped_column(String(500), default="")
    ebay_item_id: Mapped[str] = mapped_column(String(50), default="")  # für spätere API-Anbindung
    kleinanzeigen_url: Mapped[str] = mapped_column(String(500), default="")
    offered_ebay: Mapped[bool] = mapped_column(Boolean, default=False)
    offered_kleinanzeigen: Mapped[bool] = mapped_column(Boolean, default=False)

    # Freie Schlagworte (kommagetrennt)
    tags: Mapped[str] = mapped_column(String(300), default="")

    # Lagerplatz (strukturiert)
    storage_area: Mapped[str] = mapped_column(String(80), default="")    # Bereich
    storage_shelf: Mapped[str] = mapped_column(String(40), default="")   # Regal
    storage_bin: Mapped[str] = mapped_column(String(40), default="")     # Fach

    # Freies Notizfeld für Sonderfälle
    note: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    # --- Alt-Felder (vor Einführung der Verkaufshistorie) --------------------
    # Werden nur noch von der einmaligen Datenmigration gelesen; neue Verkäufe
    # landen ausschließlich in der Tabelle `sales`.
    sold_price: Mapped[float] = mapped_column(Float, default=0.0)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    sale_platform: Mapped[str] = mapped_column(String(30), default="")
    buyer_name: Mapped[str] = mapped_column(String(150), default="")
    buyer_address: Mapped[str] = mapped_column(Text, default="")
    payment_method: Mapped[str] = mapped_column(String(80), default="")
    tracking_carrier: Mapped[str] = mapped_column(String(80), default="")
    tracking_number: Mapped[str] = mapped_column(String(100), default="")
    order_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    images: Mapped[list["ArticleImage"]] = relationship(
        back_populates="article",
        cascade="all, delete-orphan",
        order_by="ArticleImage.position, ArticleImage.id",
    )
    sales: Mapped[list["Sale"]] = relationship(
        back_populates="article",
        cascade="all, delete-orphan",
        order_by="Sale.sold_at",
    )

    # --- Kennzahlen aus der Verkaufshistorie ---------------------------------
    @property
    def has_sales(self) -> bool:
        return bool(self.sales)

    @property
    def in_stock(self) -> bool:
        return self.quantity > 0

    @property
    def sold_quantity(self) -> int:
        return sum(s.quantity for s in self.sales)

    @property
    def revenue(self) -> float:
        """Summe aller Verkaufserlöse dieses Artikels."""
        return round(sum(s.sold_price for s in self.sales), 2)

    @property
    def total_profit(self) -> float | None:
        """Summe der Gewinne aller Verkäufe (None, wenn noch nichts verkauft)."""
        if not self.sales:
            return None
        return round(sum(s.profit for s in self.sales), 2)

    @property
    def last_sold_at(self) -> datetime | None:
        dates = [s.sold_at for s in self.sales if s.sold_at]
        return max(dates) if dates else None

    @property
    def stock_value(self) -> float:
        """Im Bestand gebundenes Kapital (Einkauf × Stück)."""
        return round(self.purchase_cost * self.quantity, 2)

    @property
    def tag_list(self) -> list[str]:
        return [t.strip() for t in self.tags.split(",") if t.strip()]

    @property
    def storage_location(self) -> str:
        """Lagerplatz als kompakter Text (leere Teile werden ausgelassen)."""
        return format_storage_label(self.storage_area, self.storage_shelf, self.storage_bin)


class Sale(Base):
    """Ein einzelner Verkaufsvorgang zu einem Artikel."""
    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)

    quantity: Mapped[int] = mapped_column(default=1)          # verkaufte Stückzahl
    sold_price: Mapped[float] = mapped_column(Float, default=0.0)        # Gesamterlös dieses Verkaufs
    unit_purchase_cost: Mapped[float] = mapped_column(Float, default=0.0)  # Einkauf je Stück (Snapshot)
    fees: Mapped[float] = mapped_column(Float, default=0.0)

    shipping_method: Mapped[str] = mapped_column(String(100), default="")
    shipping_cost: Mapped[float] = mapped_column(Float, default=0.0)
    shipping_payer: Mapped[str] = mapped_column(String(20), default="Käufer")

    sale_platform: Mapped[str] = mapped_column(String(30), default="")
    buyer_name: Mapped[str] = mapped_column(String(150), default="")
    buyer_address: Mapped[str] = mapped_column(Text, default="")
    payment_method: Mapped[str] = mapped_column(String(80), default="")
    tracking_carrier: Mapped[str] = mapped_column(String(80), default="")
    tracking_number: Mapped[str] = mapped_column(String(100), default="")
    note: Mapped[str] = mapped_column(Text, default="")

    order_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sold_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    article: Mapped["Article"] = relationship(back_populates="sales")

    @property
    def profit(self) -> float:
        """Gewinn dieses Verkaufs.

        Versandkosten mindern den Gewinn nur, wenn der Verkäufer sie trägt.
        """
        ship = self.shipping_cost if self.shipping_payer == "Verkäufer" else 0.0
        return round(
            self.sold_price - self.unit_purchase_cost * self.quantity - ship - self.fees, 2
        )

    @property
    def margin(self) -> float | None:
        if self.sold_price <= 0:
            return None
        return round(self.profit / self.sold_price * 100, 1)


def format_storage_label(area: str, shelf: str, bin_: str) -> str:
    parts = []
    if area:
        parts.append(area)
    if shelf:
        parts.append(f"Regal {shelf}")
    if bin_:
        parts.append(f"Fach {bin_}")
    return ", ".join(parts)


class StorageLocation(Base):
    """Verwalteter Lagerplatz (im Lager-Bereich angelegt, im Artikel per Auswahl)."""
    __tablename__ = "storage_locations"
    __table_args__ = (UniqueConstraint("area", "shelf", "bin", name="uq_storage_location"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    area: Mapped[str] = mapped_column(String(80), default="")
    shelf: Mapped[str] = mapped_column(String(40), default="")
    bin: Mapped[str] = mapped_column(String(40), default="")

    @property
    def label(self) -> str:
        return format_storage_label(self.area, self.shelf, self.bin)


class ArticleImage(Base):
    __tablename__ = "article_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(300))
    position: Mapped[int] = mapped_column(default=0)  # 0 = Hauptbild

    article: Mapped["Article"] = relationship(back_populates="images")

    @property
    def thumb(self) -> str:
        """Dateiname des Vorschaubildes (für Listen und Galerie)."""
        return thumb_name(self.filename)
