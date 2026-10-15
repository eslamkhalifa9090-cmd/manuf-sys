"""Posted journal entries. Rows here are created only by the approval engine."""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base

# Amounts are stored with 2 decimals and handled as floats rounded to 2 places.
Money = Numeric(18, 2, asdecimal=False)

ENTRY_POSTED = "posted"
# A voided entry was un-approved: kept for the audit trail, excluded from every balance.
ENTRY_VOID = "void"


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    memo: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[int] = mapped_column(Integer)
    source_no: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(10), default=ENTRY_POSTED, index=True)
    posted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    posted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    voided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime)

    lines: Mapped[list["JournalLine"]] = relationship(back_populates="entry", cascade="all, delete-orphan",
                                                      order_by="JournalLine.id")

    @property
    def entry_no(self) -> str:
        return f"JE-{self.id:06d}"


class JournalLine(Base):
    __tablename__ = "journal_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), index=True)
    debit: Mapped[float] = mapped_column(Money, default=0)
    credit: Mapped[float] = mapped_column(Money, default=0)
    memo: Mapped[str | None] = mapped_column(String(255))

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
