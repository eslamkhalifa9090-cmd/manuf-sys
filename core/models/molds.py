"""Molds: the register, the shot counter, maintenance, monthly amortization and settlements.

A mold's cost is its opening cost, plus approved purchase lines that name it, plus
approved overhauls. Its life and usage are counted in the mold's own counter unit
(piece or pair); a usage entry is typed in press shots and converted with the
working cavities. Amortization is by units of production and prospective: each
month's rate is the remaining depreciable amount over the remaining life.
"""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base
from core.models.documents import DocumentMixin
from core.models.inventory import Qty, UnitCost
from core.models.ledger import Money

FACTORY = "factory"
AGREED = "agreed"
CUSTOMER_OWNED = "customer"
OWNERSHIP = {FACTORY: "ملك المصنع", AGREED: "ملك المصنع بحد أدنى متفق عليه مع العميل", CUSTOMER_OWNED: "ملك العميل"}
AMORTIZED = (FACTORY, AGREED)

PIECE = "piece"
PAIR = "pair"
COUNTER_UNITS = {PIECE: "قطعة", PAIR: "جوز"}
PIECES_PER_COUNTER = {PIECE: 1, PAIR: 2}

ACTIVE = "active"
RETIRED = "retired"
MOLD_STATUSES = {ACTIVE: "شغالة", RETIRED: "مشطوبة / خارج الخدمة"}


class Mold(Base):
    __tablename__ = "molds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    ownership: Mapped[str] = mapped_column(String(10), default=FACTORY)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id", ondelete="RESTRICT"))
    # The customer the mold is made for (owner, or party to the agreed minimum).
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id", ondelete="RESTRICT"))
    purchase_date: Mapped[date | None] = mapped_column(Date)
    cavities: Mapped[int] = mapped_column(Integer, default=1)
    counter_unit: Mapped[str] = mapped_column(String(10), default=PIECE)
    # Useful life and agreed minimum, in counter units.
    useful_life: Mapped[float] = mapped_column(Qty, default=0)
    salvage_value: Mapped[float] = mapped_column(Money, default=0)
    agreed_qty: Mapped[float] = mapped_column(Qty, default=0)
    agreement_end: Mapped[date | None] = mapped_column(Date)
    # Balances brought in for a mold that existed before the system (its GL side comes from the opening journal).
    opening_date: Mapped[date | None] = mapped_column(Date)
    opening_cost: Mapped[float] = mapped_column(Money, default=0)
    opening_amortization: Mapped[float] = mapped_column(Money, default=0)
    opening_usage: Mapped[float] = mapped_column(Qty, default=0)
    products: Mapped[str | None] = mapped_column(String(255))
    image: Mapped[bytes | None] = mapped_column(LargeBinary)
    image_mime: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(10), default=ACTIVE)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MoldUsage(Base):
    """Shots run on a mold; units = shots x working cavities, in the mold's counter unit."""

    __tablename__ = "mold_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mold_id: Mapped[int] = mapped_column(ForeignKey("molds.id", ondelete="RESTRICT"), index=True)
    usage_date: Mapped[date] = mapped_column(Date, index=True)
    shots: Mapped[float] = mapped_column(Qty)
    cavities: Mapped[int] = mapped_column(Integer)
    pieces: Mapped[float] = mapped_column(Qty)
    units: Mapped[float] = mapped_column(Qty)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="RESTRICT"))
    # Written by an approved production order, which carries its own amortization (Dr 1303);
    # the monthly amortization document covers only usage typed in by hand.
    production_order_id: Mapped[int | None] = mapped_column(ForeignKey("production_orders.id", ondelete="CASCADE"),
                                                            index=True)
    memo: Mapped[str | None] = mapped_column(String(255))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


ROUTINE = "routine"
OVERHAUL = "overhaul"
MAINTENANCE_KINDS = {ROUTINE: "صيانة دورية (مصروف)", OVERHAUL: "تعمير يزوّد العمر (يتضاف على التكلفة)"}


class MoldMaintenance(DocumentMixin, Base):
    """Routine maintenance (expense 5205) or an overhaul (added to 1106, extends the life)."""

    __tablename__ = "mold_maintenance"
    PREFIX = "MM"

    mold_id: Mapped[int] = mapped_column(ForeignKey("molds.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(10), default=ROUTINE)
    amount: Mapped[float] = mapped_column(Money, default=0)
    extra_life: Mapped[float] = mapped_column(Qty, default=0)
    # Paid from (cash, bank, wallet) or owed to (suppliers 2101 with a party).
    counter_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id", ondelete="RESTRICT"))


class MoldAmortization(DocumentMixin, Base):
    """One month's amortization for every amortized mold that ran in the month."""

    __tablename__ = "mold_amortizations"
    __table_args__ = (UniqueConstraint("year", "month"),)
    PREFIX = "MA"

    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    lines: Mapped[list["MoldAmortizationLine"]] = relationship(back_populates="doc", cascade="all, delete-orphan",
                                                              order_by="MoldAmortizationLine.id")


class MoldAmortizationLine(Base):
    __tablename__ = "mold_amortization_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("mold_amortizations.id", ondelete="CASCADE"), index=True)
    mold_id: Mapped[int] = mapped_column(ForeignKey("molds.id", ondelete="RESTRICT"))
    units: Mapped[float] = mapped_column(Qty)
    rate: Mapped[float] = mapped_column(UnitCost)
    amount: Mapped[float] = mapped_column(Money)

    doc: Mapped[MoldAmortization] = relationship(back_populates="lines")


class MoldSettlement(DocumentMixin, Base):
    """Settles a mold: charge the customer the recovery (Dr 1401 / Cr 4301), retire it (book value to 5401), or both."""

    __tablename__ = "mold_settlements"
    PREFIX = "MS"

    mold_id: Mapped[int] = mapped_column(ForeignKey("molds.id", ondelete="RESTRICT"), index=True)
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id", ondelete="RESTRICT"))
    recovery_amount: Mapped[float] = mapped_column(Money, default=0)
    retire: Mapped[bool] = mapped_column(Boolean, default=False)
    # Snapshot shown to the approver; posting recomputes cost and accumulated amortization.
    agreed_qty: Mapped[float] = mapped_column(Qty, default=0)
    used_units: Mapped[float] = mapped_column(Qty, default=0)
    book_value: Mapped[float] = mapped_column(Money, default=0)
