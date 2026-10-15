"""Fixed-asset register, monthly straight-line depreciation and the year-end close.

An asset's cost reaches the books through its purchase (a direct-account line) or the
opening journal; the register holds what depreciation needs. Each month's depreciation is
one document: Dr 5207 (factory) or 6111 (administration) / Cr the category's accumulated
depreciation (12xx).
"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base
from core.models.documents import DocumentMixin
from core.models.ledger import Money

FACTORY = "factory"
ADMIN = "admin"
ASSET_DEPARTMENTS = {FACTORY: "المصنع (5207)", ADMIN: "الإدارة (6111)"}

ASSET_ACTIVE = "active"
ASSET_DISPOSED = "disposed"
ASSET_STATUSES = {ASSET_ACTIVE: "في الخدمة", ASSET_DISPOSED: "مستبعد"}

# Asset account system key -> its accumulated depreciation system key. Molds (1106) have their own register.
CATEGORY_KEYS = {"fa_machinery": "ad_machinery", "fa_tools": "ad_tools", "fa_electrical": "ad_electrical",
                 "fa_electronics": "ad_electronics", "fa_furniture": "ad_furniture", "fa_vehicles": "ad_vehicles"}


class FixedAsset(Base):
    __tablename__ = "fixed_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    department: Mapped[str] = mapped_column(String(10), default=FACTORY)
    acquisition_date: Mapped[date] = mapped_column(Date)
    cost: Mapped[float] = mapped_column(Money)
    salvage_value: Mapped[float] = mapped_column(Money, default=0)
    life_months: Mapped[int] = mapped_column(Integer)
    # Depreciation runs from this month on.
    depreciation_start: Mapped[date] = mapped_column(Date)
    # An asset already in use at cut-over: what was depreciated up to the opening date.
    opening_date: Mapped[date | None] = mapped_column(Date)
    opening_accumulated: Mapped[float] = mapped_column(Money, default=0)
    status: Mapped[str] = mapped_column(String(10), default=ASSET_ACTIVE)
    disposal_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Depreciation(DocumentMixin, Base):
    """One month's depreciation for every asset in service."""

    __tablename__ = "depreciations"
    __table_args__ = (UniqueConstraint("year", "month"),)
    PREFIX = "DP"

    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    lines: Mapped[list["DepreciationLine"]] = relationship(back_populates="doc", cascade="all, delete-orphan",
                                                          order_by="DepreciationLine.id")


class DepreciationLine(Base):
    __tablename__ = "depreciation_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("depreciations.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("fixed_assets.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[float] = mapped_column(Money)

    doc: Mapped[Depreciation] = relationship(back_populates="lines")


class YearClose(DocumentMixin, Base):
    """Closes the income-statement accounts of a fiscal year into retained earnings (3301)."""

    __tablename__ = "year_closes"
    __table_args__ = (UniqueConstraint("year"),)
    PREFIX = "YC"

    # The calendar year the fiscal year starts in.
    year: Mapped[int] = mapped_column(Integer)
    date_from: Mapped[date] = mapped_column(Date)
