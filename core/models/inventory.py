"""Items, item groups, the stock ledger, and the documents that move stock.

Stock is valued at weighted average cost (WAC). Moves are written only by the
approval engine; voided moves (un-approved documents) are kept for audit and
excluded from every quantity and value.
"""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base
from core.models.documents import DocumentMixin
from core.models.ledger import Money

Qty = Numeric(18, 4, asdecimal=False)
UnitCost = Numeric(18, 6, asdecimal=False)

GROUP_KINDS = {
    "raw": "خامات", "compound": "تضريبة وخامات وسيطة", "finished": "إنتاج تام", "spares": "قطع غيار ومهمات",
    "packaging": "مستلزمات تعبئة", "accessories": "اكسسوارات", "samples": "عينات وهدايا واردة",
}


class ItemGroup(Base):
    __tablename__ = "item_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    inventory_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("item_groups.id", ondelete="RESTRICT"), index=True)
    unit: Mapped[str] = mapped_column(String(20), default="عدد")
    family: Mapped[str | None] = mapped_column(String(100))
    min_qty: Mapped[float] = mapped_column(Qty, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    group: Mapped[ItemGroup] = relationship()


MOVE_POSTED = "posted"
MOVE_VOID = "void"


class StockMove(Base):
    __tablename__ = "stock_moves"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="RESTRICT"), index=True)
    move_date: Mapped[date] = mapped_column(Date, index=True)
    qty_in: Mapped[float] = mapped_column(Qty, default=0)
    qty_out: Mapped[float] = mapped_column(Qty, default=0)
    unit_cost: Mapped[float] = mapped_column(UnitCost, default=0)
    value_in: Mapped[float] = mapped_column(Money, default=0)
    value_out: Mapped[float] = mapped_column(Money, default=0)
    source_type: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    source_no: Mapped[str | None] = mapped_column(String(30))
    kind: Mapped[str | None] = mapped_column(String(20))
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"))
    memo: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(10), default=MOVE_POSTED, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


# ---------------------------------------------------------------- purchases

PURCHASE_INVOICE = "invoice"
PURCHASE_RETURN = "return"
PURCHASE_KINDS = {PURCHASE_INVOICE: "فاتورة مشتريات", PURCHASE_RETURN: "مرتجع مشتريات"}
CREDIT = "credit"
CASH = "cash"
PAYMENT_TERMS = {CREDIT: "آجل", CASH: "نقدي"}


class PurchaseInvoice(DocumentMixin, Base):
    __tablename__ = "purchase_invoices"

    kind: Mapped[str] = mapped_column(String(10), default=PURCHASE_INVOICE)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="RESTRICT"))
    payment: Mapped[str] = mapped_column(String(10), default=CREDIT)
    treasury_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    supplier_ref: Mapped[str | None] = mapped_column(String(60))
    # Trade discount on the whole invoice, spread over the lines by value (lowers item cost).
    discount: Mapped[float] = mapped_column(Money, default=0)
    lines: Mapped[list["PurchaseLine"]] = relationship(back_populates="invoice", cascade="all, delete-orphan",
                                                      order_by="PurchaseLine.id")

    @property
    def doc_no(self) -> str:
        return f"{'PR' if self.kind == PURCHASE_RETURN else 'PI'}-{self.id:06d}"


class PurchaseLine(Base):
    """Either a stock item (goes to inventory at cost) or a direct account (fixed asset or expense)."""

    __tablename__ = "purchase_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("purchase_invoices.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="RESTRICT"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    description: Mapped[str | None] = mapped_column(String(255))
    qty: Mapped[float] = mapped_column(Qty, default=1)
    unit_price: Mapped[float] = mapped_column(UnitCost, default=0)

    invoice: Mapped[PurchaseInvoice] = relationship(back_populates="lines")

    @property
    def amount(self) -> float:
        return round(float(self.qty or 0) * float(self.unit_price or 0), 2)


# ---------------------------------------------------------------- stock vouchers

STOCK_OPENING = "opening"
GIFT_IN = "gift_in"
GIFT_OUT = "gift_out"
ISSUE = "issue"
COUNT_GAIN = "count_gain"
COUNT_LOSS = "count_loss"
STOCK_KINDS = {
    STOCK_OPENING: "رصيد افتتاحي مخزن",
    GIFT_IN: "هدايا وعينات واردة",
    GIFT_OUT: "هدايا وعينات صادرة",
    ISSUE: "صرف مخزني",
    COUNT_GAIN: "تسوية جرد بالزيادة",
    COUNT_LOSS: "تسوية جرد بالعجز",
}
INBOUND_KINDS = (STOCK_OPENING, GIFT_IN, COUNT_GAIN)


class StockVoucher(DocumentMixin, Base):
    __tablename__ = "stock_vouchers"

    kind: Mapped[str] = mapped_column(String(12))
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id", ondelete="RESTRICT"))
    # Debit side of an issue (e.g. maintenance expense); fixed accounts are used for the other kinds.
    counter_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    lines: Mapped[list["StockVoucherLine"]] = relationship(back_populates="voucher", cascade="all, delete-orphan",
                                                          order_by="StockVoucherLine.id")

    PREFIXES = {STOCK_OPENING: "OS", GIFT_IN: "GR", GIFT_OUT: "GI", ISSUE: "SI", COUNT_GAIN: "CG", COUNT_LOSS: "CL"}

    @property
    def doc_no(self) -> str:
        return f"{self.PREFIXES.get(self.kind, 'SV')}-{self.id:06d}"


class StockVoucherLine(Base):
    __tablename__ = "stock_voucher_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    voucher_id: Mapped[int] = mapped_column(ForeignKey("stock_vouchers.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="RESTRICT"))
    qty: Mapped[float] = mapped_column(Qty)
    # Entered cost for opening balances and count gains with no average yet; otherwise WAC at approval.
    unit_cost: Mapped[float | None] = mapped_column(UnitCost)
    memo: Mapped[str | None] = mapped_column(String(255))

    voucher: Mapped[StockVoucher] = relationship(back_populates="lines")
