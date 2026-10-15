"""Source documents that go through the approval workflow.

Every document is saved as pending. Only approval turns it into a journal entry;
until then it affects no balance, report or trial balance.
"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from core.db import Base
from core.models.ledger import Money

PENDING = "pending"
RETURNED = "returned"
APPROVED = "approved"

STATUS_LABELS = {PENDING: "في انتظار الاعتماد", RETURNED: "مرتجع للتعديل", APPROVED: "معتمد"}


class DocumentMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_date: Mapped[date] = mapped_column(Date, index=True)
    memo: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default=PENDING, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    returned_at: Mapped[datetime | None] = mapped_column(DateTime)
    rejection_note: Mapped[str | None] = mapped_column(Text)

    @declared_attr
    def created_by_id(cls) -> Mapped[int]:
        return mapped_column(ForeignKey("users.id"))

    @declared_attr
    def approved_by_id(cls) -> Mapped[int | None]:
        return mapped_column(ForeignKey("users.id"))

    @declared_attr
    def returned_by_id(cls) -> Mapped[int | None]:
        return mapped_column(ForeignKey("users.id"))

    @declared_attr
    def journal_entry_id(cls) -> Mapped[int | None]:
        return mapped_column(ForeignKey("journal_entries.id"))

    PREFIX = "DOC"

    @property
    def doc_no(self) -> str:
        return f"{self.PREFIX}-{self.id:06d}"


JOURNAL_REGULAR = "regular"
JOURNAL_OPENING = "opening"
JOURNAL_KINDS = {JOURNAL_REGULAR: "قيد يومية", JOURNAL_OPENING: "قيد أرصدة افتتاحية"}


class ManualJournal(DocumentMixin, Base):
    __tablename__ = "manual_journals"
    PREFIX = "JV"

    kind: Mapped[str] = mapped_column(String(12), default=JOURNAL_REGULAR)
    lines: Mapped[list["ManualJournalLine"]] = relationship(
        back_populates="journal", cascade="all, delete-orphan", order_by="ManualJournalLine.id"
    )

    @property
    def doc_no(self) -> str:
        return f"{'OB' if self.kind == JOURNAL_OPENING else 'JV'}-{self.id:06d}"


class ManualJournalLine(Base):
    __tablename__ = "manual_journal_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    journal_id: Mapped[int] = mapped_column(ForeignKey("manual_journals.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    debit: Mapped[float] = mapped_column(Money, default=0)
    credit: Mapped[float] = mapped_column(Money, default=0)
    memo: Mapped[str | None] = mapped_column(String(255))
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id", ondelete="RESTRICT"))

    journal: Mapped[ManualJournal] = relationship(back_populates="lines")


RECEIPT = "receipt"
PAYMENT = "payment"
VOUCHER_KINDS = {RECEIPT: "سند قبض", PAYMENT: "سند صرف"}


class CashVoucher(DocumentMixin, Base):
    """Cash, wallet or bank receipt/payment against one counter account (like a cash-book row)."""

    __tablename__ = "cash_vouchers"

    kind: Mapped[str] = mapped_column(String(10))
    treasury_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    counter_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    amount: Mapped[float] = mapped_column(Money)
    party_name: Mapped[str | None] = mapped_column(String(150))
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id", ondelete="RESTRICT"))
    # Discount allowed (receipt from a customer) or received (payment to a supplier), on top of `amount`.
    discount: Mapped[float] = mapped_column(Money, default=0)
    reference: Mapped[str | None] = mapped_column(String(60))

    @property
    def doc_no(self) -> str:
        return f"{'RV' if self.kind == RECEIPT else 'PV'}-{self.id:06d}"
