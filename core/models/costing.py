"""Bills of materials, conversion and overhead rates, and price quotes.

A BOM (recipe) says what one batch of an item is made of. A quote is a frozen
snapshot of a costing: every price and rate it used is stored on it, so a later
change to the average cost or to a rate never changes a saved quote. Editing a
saved quote makes a new revision; older revisions stay read-only.
"""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base
from core.models.inventory import Item, Qty, UnitCost

Pct = Numeric(9, 4, asdecimal=False)


class Bom(Base):
    __tablename__ = "boms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), unique=True)
    # The recipe makes this many pieces of the item (for a kilo item, a piece is one kilo).
    output_qty: Mapped[float] = mapped_column(Qty, default=1)
    # Normal waste, % added on materials and conversion.
    waste_pct: Mapped[float] = mapped_column(Pct, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    item: Mapped[Item] = relationship()
    lines: Mapped[list["BomLine"]] = relationship(back_populates="bom", cascade="all, delete-orphan",
                                                  order_by="BomLine.id")


class BomLine(Base):
    __tablename__ = "bom_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bom_id: Mapped[int] = mapped_column(ForeignKey("boms.id", ondelete="CASCADE"), index=True)
    component_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="RESTRICT"))
    qty: Mapped[float] = mapped_column(Qty)
    # True: qty is grams (component counted in kilos or grams); False: qty is in the component's own unit.
    in_grams: Mapped[bool] = mapped_column(Boolean, default=True)

    bom: Mapped[Bom] = relationship(back_populates="lines")
    component: Mapped[Item] = relationship()


# ---------------------------------------------------------------- rates

WAGES = "wages"
ENERGY = "energy"
MAINTENANCE = "maintenance"
OTHER = "other"
GA = "ga"
SELLING = "selling"
CONVERSION = (WAGES, ENERGY, MAINTENANCE, OTHER)
RATE_COMPONENTS = {
    WAGES: "أجور مباشرة", ENERGY: "طاقة (كهرباء وغاز وسولار)", MAINTENANCE: "صيانة",
    OTHER: "تكاليف صناعية أخرى وإهلاك", GA: "مصروفات عمومية وإدارية", SELLING: "مصروفات بيع وتسويق",
}
PER_PIECE = "per_piece"
PCT = "pct"
BASES = {PER_PIECE: "مبلغ للقطعة", PCT: "% من تكلفة الإنتاج"}


class CostRate(Base):
    """A predetermined rate, effective from a date until the next rate of the same component."""

    __tablename__ = "cost_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    component: Mapped[str] = mapped_column(String(20), index=True)
    basis: Mapped[str] = mapped_column(String(10), default=PER_PIECE)
    value: Mapped[float] = mapped_column(UnitCost, default=0)
    valid_from: Mapped[date] = mapped_column(Date, index=True)
    notes: Mapped[str | None] = mapped_column(String(255))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


# ---------------------------------------------------------------- quotes

WAC = "wac"
LAST = "last"
MANUAL = "manual"
METHODS = {WAC: "المتوسط المرجح", LAST: "آخر سعر شراء", MANUAL: "يدوي"}

MOLD_ORDER = "order"
MOLD_LIFE = "life"
MOLD_NONE = "none"
MOLD_MODES = {MOLD_ORDER: "تحميل كامل على الطلبية", MOLD_LIFE: "توزيع على العمر الافتراضي",
              MOLD_NONE: "بدون تحميل (ملك العميل أو مباعة له)"}

Q_DRAFT = "draft"
Q_SENT = "sent"
Q_ACCEPTED = "accepted"
Q_REJECTED = "rejected"
QUOTE_STATUSES = {Q_DRAFT: "مسودة", Q_SENT: "اتبعت للعميل", Q_ACCEPTED: "اتقبل", Q_REJECTED: "اترفض"}


class PriceQuote(Base):
    __tablename__ = "price_quotes"
    __table_args__ = (UniqueConstraint("quote_no", "revision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_no: Mapped[int] = mapped_column(Integer, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    status: Mapped[str] = mapped_column(String(10), default=Q_DRAFT)
    quote_date: Mapped[date] = mapped_column(Date)
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id", ondelete="RESTRICT"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="RESTRICT"))
    # Ordered quantity in the item's selling unit (e.g. pairs); pieces = qty x pieces_per_unit.
    qty: Mapped[float] = mapped_column(Qty, default=0)
    pieces_per_unit: Mapped[float] = mapped_column(Qty, default=1)
    piece_weight_g: Mapped[float | None] = mapped_column(Qty)
    method: Mapped[str] = mapped_column(String(10), default=WAC)
    waste_pct: Mapped[float] = mapped_column(Pct, default=0)
    wages: Mapped[float] = mapped_column(UnitCost, default=0)
    energy: Mapped[float] = mapped_column(UnitCost, default=0)
    maintenance: Mapped[float] = mapped_column(UnitCost, default=0)
    other: Mapped[float] = mapped_column(UnitCost, default=0)
    mold_mode: Mapped[str] = mapped_column(String(10), default=MOLD_NONE)
    mold_name: Mapped[str | None] = mapped_column(String(150))
    mold_cost: Mapped[float] = mapped_column(UnitCost, default=0)
    mold_life_pieces: Mapped[float] = mapped_column(Qty, default=0)
    ga_basis: Mapped[str] = mapped_column(String(10), default=PCT)
    ga_value: Mapped[float] = mapped_column(UnitCost, default=0)
    selling_basis: Mapped[str] = mapped_column(String(10), default=PCT)
    selling_value: Mapped[float] = mapped_column(UnitCost, default=0)
    margin_pct: Mapped[float] = mapped_column(Pct, default=0)
    # The price offered to the customer per selling unit; empty means the suggested price.
    offered_price: Mapped[float | None] = mapped_column(UnitCost)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    item: Mapped[Item] = relationship()
    lines: Mapped[list["PriceQuoteLine"]] = relationship(back_populates="quote", cascade="all, delete-orphan",
                                                         order_by="PriceQuoteLine.id")

    @property
    def doc_no(self) -> str:
        return f"Q-{self.quote_no:06d}-v{self.revision}"


class PriceQuoteLine(Base):
    """One material per piece, with the three prices frozen when the quote was saved."""

    __tablename__ = "price_quote_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("price_quotes.id", ondelete="CASCADE"), index=True)
    component_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="RESTRICT"))
    # Quantity per piece in the component's own unit (grams already converted to kilos).
    qty: Mapped[float] = mapped_column(UnitCost)
    wac_price: Mapped[float] = mapped_column(UnitCost, default=0)
    last_price: Mapped[float] = mapped_column(UnitCost, default=0)
    manual_price: Mapped[float] = mapped_column(UnitCost, default=0)

    quote: Mapped[PriceQuote] = relationship(back_populates="lines")
    component: Mapped[Item] = relationship()
