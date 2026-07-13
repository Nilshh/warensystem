"""Datenmodell."""
from datetime import datetime, timezone

from sqlalchemy import String, Float, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

# Lebenszyklus eines Artikels
STATUSES = ["Entwurf", "Angeboten", "Reserviert", "Verkauft", "Archiviert"]

# Gängige Zustände (frei erweiterbar im Formular)
CONDITIONS = ["Neu", "Neuwertig", "Gebraucht", "Defekt", "Ersatzteil"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
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
    shipping_cost: Mapped[float] = mapped_column(Float, default=0.0)   # eigene Versandkosten
    fees: Mapped[float] = mapped_column(Float, default=0.0)            # Plattformgebühren

    # Plattform-Links (parallel möglich)
    ebay_url: Mapped[str] = mapped_column(String(500), default="")
    ebay_item_id: Mapped[str] = mapped_column(String(50), default="")  # für spätere API-Anbindung
    kleinanzeigen_url: Mapped[str] = mapped_column(String(500), default="")
    offered_ebay: Mapped[bool] = mapped_column(Boolean, default=False)
    offered_kleinanzeigen: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    images: Mapped[list["ArticleImage"]] = relationship(
        back_populates="article",
        cascade="all, delete-orphan",
        order_by="ArticleImage.id",
    )

    @property
    def profit(self) -> float | None:
        """Gewinn nach Kosten & Gebühren (nur wenn verkauft)."""
        if self.status != "Verkauft":
            return None
        return round(self.sold_price - self.purchase_cost - self.shipping_cost - self.fees, 2)


class ArticleImage(Base):
    __tablename__ = "article_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(300))

    article: Mapped["Article"] = relationship(back_populates="images")
