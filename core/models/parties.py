from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base

CUSTOMER = "customer"
SUPPLIER = "supplier"
BOTH = "both"
PARTY_KINDS = {CUSTOMER: "عميل", SUPPLIER: "مورد", BOTH: "عميل ومورد"}


class Party(Base):
    """A customer or supplier. Their balances live on the control accounts (1401 / 2101),
    tagged per party on each journal line: a subsidiary ledger, not one GL account each."""

    __tablename__ = "parties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    kind: Mapped[str] = mapped_column(String(10), default=CUSTOMER)
    phone: Mapped[str | None] = mapped_column(String(60))
    address: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
