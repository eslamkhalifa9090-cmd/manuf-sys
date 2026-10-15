"""Customer orders, sales invoices and sales returns.

A customer order records what the customer asked for; it posts nothing. A sales invoice
is also the delivery note: on approval it posts the sale and takes the goods out of stock
at weighted average cost. A return brings goods back at the cost they left with.
"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base
from core.models.documents import DocumentMixin
from core.models.inventory import CREDIT, Qty, UnitCost
from core.models.ledger import Money

SO_OPEN = "open"
SO_CLOSED = "closed"
SO_CANCELLED = "cancelled"
ORDER_STATUSES = {SO_OPEN: "مفتوح", SO_CLOSED: "مقفول", SO_CANCELLED: "ملغي"}


class SalesOrder(Base):
    __tablename__ = "sales_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_date: Mapped[date] = mapped_column(Date, index=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="RESTRICT"), index=True)
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("price_quotes.id", ondelete="SET NULL"))
    delivery_date: Mapped[date | None] = mapped_column(Date)
    customer_ref: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(10), default=SO_OPEN, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    lines: Mapped[list["SalesOrderLine"]] = relationship(back_populates="order", cascade="all, delete-orphan",
                                                         order_by="SalesOrderLine.id")

    @property
    def doc_no(self) -> str:
        return f"SO-{self.id:06d}"


class SalesOrderLine(Base):
    __tablename__ = "sales_order_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="RESTRICT"))
    mold_id: Mapped[int | None] = mapped_column(ForeignKey("molds.id", ondelete="SET NULL"))
    # In the item's own unit (pairs for a shoe sole).
    qty: Mapped[float] = mapped_column(Qty)
    unit_price: Mapped[float] = mapped_column(UnitCost, default=0)
    description: Mapped[str | None] = mapped_column(String(255))

    order: Mapped[SalesOrder] = relationship(back_populates="lines")


SALE_INVOICE = "invoice"
SALE_RETURN = "return"
SALE_KINDS = {SALE_INVOICE: "فاتورة مبيعات", SALE_RETURN: "مرتجع مبيعات"}


class SalesInvoice(DocumentMixin, Base):
    __tablename__ = "sales_invoices"

    kind: Mapped[str] = mapped_column(String(10), default=SALE_INVOICE)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="RESTRICT"), index=True)
    payment: Mapped[str] = mapped_column(String(10), default=CREDIT)
    treasury_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    sales_order_id: Mapped[int | None] = mapped_column(ForeignKey("sales_orders.id", ondelete="RESTRICT"), index=True)
    # A return may name the invoice it reverses; its goods come back at that invoice's cost.
    return_of_id: Mapped[int | None] = mapped_column(ForeignKey("sales_invoices.id", ondelete="RESTRICT"))
    # The invoice doubles as the delivery note; this is the paper dispatch number, if any.
    dispatch_no: Mapped[str | None] = mapped_column(String(40))
    # Discount allowed on the whole invoice (posted to 4202).
    discount: Mapped[float] = mapped_column(Money, default=0)
    lines: Mapped[list["SalesLine"]] = relationship(back_populates="invoice", cascade="all, delete-orphan",
                                                   order_by="SalesLine.id")

    @property
    def doc_no(self) -> str:
        return f"{'SR' if self.kind == SALE_RETURN else 'SI'}-{self.id:06d}"


class SalesLine(Base):
    """A stock item sold (or returned), or a mold sold to the customer (4102)."""

    __tablename__ = "sales_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("sales_invoices.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="RESTRICT"))
    mold_id: Mapped[int | None] = mapped_column(ForeignKey("molds.id", ondelete="RESTRICT"))
    order_line_id: Mapped[int | None] = mapped_column(ForeignKey("sales_order_lines.id", ondelete="SET NULL"))
    description: Mapped[str | None] = mapped_column(String(255))
    qty: Mapped[float] = mapped_column(Qty, default=1)
    unit_price: Mapped[float] = mapped_column(UnitCost, default=0)
    # Cost per unit fixed at approval (stock leaves at WAC); a linked return takes it back at this cost.
    unit_cost: Mapped[float | None] = mapped_column(UnitCost)

    invoice: Mapped[SalesInvoice] = relationship(back_populates="lines")

    @property
    def amount(self) -> float:
        return round(float(self.qty or 0) * float(self.unit_price or 0), 2)
