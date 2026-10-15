"""Production: compound runs (stage 1), production orders (stage 2) and the month-end overhead close.

Both stages pass through work in process (1303) inside one approved document: materials go
in at weighted average cost, conversion is applied at predetermined rates (Cr 5301), and the
output comes out at the total. A production order also takes its mold's amortization for the
shots it ran (Dr 1303 / Cr 1206), and waste above the normal rate goes to 5402.
"""
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base
from core.models.documents import DocumentMixin
from core.models.inventory import Qty, UnitCost
from core.models.ledger import Money
from core.models.costing import Pct


class CompoundRun(DocumentMixin, Base):
    """Stage 1: materials mixed into a compound (kilos)."""

    __tablename__ = "compound_runs"
    PREFIX = "CR"

    output_item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="RESTRICT"))
    output_qty: Mapped[float] = mapped_column(Qty)
    # Optional conversion applied per unit of output (per kilo); zero by default.
    conversion_rate: Mapped[float] = mapped_column(UnitCost, default=0)
    lines: Mapped[list["CompoundRunLine"]] = relationship(back_populates="run", cascade="all, delete-orphan",
                                                         order_by="CompoundRunLine.id")


class CompoundRunLine(Base):
    __tablename__ = "compound_run_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("compound_runs.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="RESTRICT"))
    qty: Mapped[float] = mapped_column(Qty)

    run: Mapped[CompoundRun] = relationship(back_populates="lines")


class ProductionOrder(DocumentMixin, Base):
    """Stage 2: a run of finished pieces on a mold, usually for a customer order."""

    __tablename__ = "production_orders"
    PREFIX = "PO"

    sales_order_id: Mapped[int | None] = mapped_column(ForeignKey("sales_orders.id", ondelete="RESTRICT"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="RESTRICT"), index=True)
    mold_id: Mapped[int | None] = mapped_column(ForeignKey("molds.id", ondelete="RESTRICT"), index=True)
    shots: Mapped[float] = mapped_column(Qty, default=0)
    cavities: Mapped[int] = mapped_column(Integer, default=1)
    # Pieces: good ones go to finished stock (in the item's unit), waste is what was scrapped.
    good_pieces: Mapped[float] = mapped_column(Qty, default=0)
    waste_pieces: Mapped[float] = mapped_column(Qty, default=0)
    # Normal waste, % of good pieces; above it is abnormal (5402).
    normal_waste_pct: Mapped[float] = mapped_column(Pct, default=0)
    # Conversion rates per piece run, snapshot of the rates in force (editable).
    wages: Mapped[float] = mapped_column(UnitCost, default=0)
    energy: Mapped[float] = mapped_column(UnitCost, default=0)
    maintenance: Mapped[float] = mapped_column(UnitCost, default=0)
    other: Mapped[float] = mapped_column(UnitCost, default=0)
    # Set at approval: the mold's units run, rate and amortization charged to this order.
    mold_units: Mapped[float] = mapped_column(Qty, default=0)
    mold_rate: Mapped[float] = mapped_column(UnitCost, default=0)
    mold_amount: Mapped[float] = mapped_column(Money, default=0)
    lines: Mapped[list["ProductionLine"]] = relationship(back_populates="order", cascade="all, delete-orphan",
                                                        order_by="ProductionLine.id")

    @property
    def pieces_run(self) -> float:
        return float(self.good_pieces or 0) + float(self.waste_pieces or 0)


class ProductionLine(Base):
    """A material issued to the order (compound, raw material, packaging)."""

    __tablename__ = "production_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("production_orders.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="RESTRICT"))
    qty: Mapped[float] = mapped_column(Qty)

    order: Mapped[ProductionOrder] = relationship(back_populates="lines")


class OverheadClose(DocumentMixin, Base):
    """Month-end: applied conversion (5301) against actual (5201-5208); the difference to 5404."""

    __tablename__ = "overhead_closes"
    __table_args__ = (UniqueConstraint("year", "month"),)
    PREFIX = "OC"

    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    # Snapshot shown before approval; posting recomputes from the ledger.
    applied: Mapped[float] = mapped_column(Money, default=0)
    actual: Mapped[float] = mapped_column(Money, default=0)
    note: Mapped[str | None] = mapped_column(String(255))
