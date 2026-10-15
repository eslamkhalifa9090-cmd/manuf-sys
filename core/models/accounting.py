"""Chart of accounts and fiscal periods."""
from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base

ACCOUNT_TYPES = {
    "asset": "أصول",
    "liability": "خصوم",
    "equity": "حقوق ملكية",
    "revenue": "إيرادات",
    "expense": "مصروفات وتكاليف",
}
# Which side increases the balance, by account type.
NORMAL_SIDE = {"asset": "debit", "expense": "debit", "liability": "credit", "equity": "credit", "revenue": "credit"}
STATEMENTS = {"BS": "الميزانية", "PL": "قائمة الدخل"}
PL_SECTIONS = {
    "sales": "المبيعات",
    "sales_deductions": "مردودات وخصومات المبيعات",
    "cogs": "تكلفة المبيعات",
    "ga": "مصروفات عمومية وإدارية",
    "selling": "مصروفات بيع وتسويق",
    "other_income": "إيرادات أخرى",
    "other_expense": "مصروفات أخرى",
}


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name_ar: Mapped[str] = mapped_column(String(150))
    name_en: Mapped[str | None] = mapped_column(String(150))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    level: Mapped[int] = mapped_column(Integer, default=1)
    account_type: Mapped[str] = mapped_column(String(20))
    normal_side: Mapped[str] = mapped_column(String(10))
    statement: Mapped[str] = mapped_column(String(2))
    pl_section: Mapped[str | None] = mapped_column(String(30))
    # Only leaf accounts take journal lines; parents are totals.
    is_postable: Mapped[bool] = mapped_column(Boolean, default=True)
    # Contra accounts (accumulated depreciation, sales returns) reduce their parent group.
    is_contra: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # System accounts are used by posting rules and cannot be deleted or deactivated.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    system_key: Mapped[str | None] = mapped_column(String(60), unique=True)

    parent: Mapped["Account | None"] = relationship(remote_side="Account.id", back_populates="children")
    children: Mapped[list["Account"]] = relationship(back_populates="parent")


class FiscalPeriod(Base):
    __tablename__ = "fiscal_periods"
    __table_args__ = (UniqueConstraint("year", "month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)
