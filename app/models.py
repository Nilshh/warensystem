"""Datenmodell."""
from datetime import datetime, timezone

from sqlalchemy import String, Float, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

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
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(100), default="")
    condition: Mapped[str] = mapped_column(String(50), default="")
    status: Mapped[str] = mapped_column(String(30), default="Entwurf")

    # Preise & Kosten
    purchase_cost: Mapped[float] = mapped_column(Float, default=0.0)   # Einkaufskosten
    listing_price: Mapped[float] = mapped_column(Float, default=0.0)   # Angebotspreis
    sold_price: Mapped[float] = mapped_column(Float, default=0.0)      # tatsächlicher Verkaufspreis
    shipping_method: Mapped[str] = mapped_column(String(100), default="")
    shipping_cost: Mapped[float] = mapped_column(Float, default=0.0)   # Versandkosten
    shipping_payer: Mapped[str] = mapped_column(String(20), default="Käufer")  # wer zahlt Versand
    fees: Mapped[float] = mapped_column(Float, default=0.0)            # Plattformgebühren

    # Plattform-Links (parallel möglich)
    ebay_url: Mapped[str] = mapped_column(String(500), default="")
    ebay_item_id: Mapped[str] = mapped_column(String(50), default="")  # für spätere API-Anbindung
    kleinanzeigen_url: Mapped[str] = mapped_column(String(500), default="")
    offered_ebay: Mapped[bool] = mapped_column(Boolean, default=False)
    offered_kleinanzeigen: Mapped[bool] = mapped_column(Boolean, default=False)

    # Freie Schlagworte (kommagetrennt)
    tags: Mapped[str] = mapped_column(String(300), default="")

    # Käufer- & Versandabwicklung
    sale_platform: Mapped[str] = mapped_column(String(30), default="")   # verkauft über
    buyer_name: Mapped[str] = mapped_column(String(150), default="")
    buyer_address: Mapped[str] = mapped_column(Text, default="")         # Lieferadresse
    payment_method: Mapped[str] = mapped_column(String(80), default="")
    tracking_carrier: Mapped[str] = mapped_column(String(80), default="")
    tracking_number: Mapped[str] = mapped_column(String(100), default="")
    order_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    images: Mapped[list["ArticleImage"]] = relationship(
        back_populates="article",
        cascade="all, delete-orphan",
        order_by="ArticleImage.position, ArticleImage.id",
    )

    @property
    def is_sold(self) -> bool:
        """Verkauft = hat ein Verkaufsdatum (bleibt auch nach Archivierung wahr)."""
        return self.sold_at is not None

    @property
    def profit(self) -> float | None:
        """Gewinn nach Kosten & Gebühren (sobald verkauft, auch archiviert).

        Versandkosten mindern den Gewinn nur, wenn der Verkäufer sie trägt.
        Zahlt der Käufer (Standard), sind sie durchlaufend und neutral.
        """
        if self.sold_at is None:
            return None
        ship = self.shipping_cost if self.shipping_payer == "Verkäufer" else 0.0
        return round(self.sold_price - self.purchase_cost - ship - self.fees, 2)

    @property
    def margin(self) -> float | None:
        """Gewinnmarge in % vom Verkaufspreis (nur wenn verkauft & Preis > 0)."""
        p = self.profit
        if p is None or self.sold_price <= 0:
            return None
        return round(p / self.sold_price * 100, 1)

    @property
    def tag_list(self) -> list[str]:
        return [t.strip() for t in self.tags.split(",") if t.strip()]


class ArticleImage(Base):
    __tablename__ = "article_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(300))
    position: Mapped[int] = mapped_column(default=0)  # 0 = Hauptbild

    article: Mapped["Article"] = relationship(back_populates="images")
