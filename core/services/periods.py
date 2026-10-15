"""Fiscal periods: a closed month refuses new postings, approvals and un-approvals."""
from datetime import date

import pandas as pd
from sqlalchemy import select

from core.db import session_scope
from core.models import FiscalPeriod, JournalEntry
from core.models.ledger import ENTRY_POSTED
from core.services import audit
from core.services.errors import BusinessError

MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def is_closed(session, d: date) -> bool:
    row = session.scalar(select(FiscalPeriod).where(FiscalPeriod.year == d.year, FiscalPeriod.month == d.month))
    return bool(row and row.is_closed)


def ensure_open(session, d: date) -> None:
    if is_closed(session, d):
        raise BusinessError(f"فترة {MONTHS[d.month - 1]} {d.year} مقفولة. افتحها الأول من شاشة الفترات المحاسبية.")


def periods_frame(year: int, start_month: int = 1) -> pd.DataFrame:
    """The 12 months of the fiscal year that starts in `start_month` of `year`."""
    months = [((start_month - 1 + i) % 12 + 1, year + (start_month - 1 + i) // 12) for i in range(12)]
    with session_scope() as s:
        closed = {(p.year, p.month) for p in s.scalars(select(FiscalPeriod).where(FiscalPeriod.is_closed))}
        rows = []
        for m, y in months:
            start = date(y, m, 1)
            end = date(y + (m == 12), m % 12 + 1, 1)
            n = s.query(JournalEntry).filter(JournalEntry.entry_date >= start, JournalEntry.entry_date < end,
                                             JournalEntry.status == ENTRY_POSTED).count()
            rows.append({"السنة": y, "الشهر رقم": m, "الشهر": MONTHS[m - 1], "عدد القيود": n,
                         "مقفولة": (y, m) in closed})
    return pd.DataFrame(rows)


def set_closed(actor, year: int, month: int, closed: bool) -> None:
    if not actor.can("periods", "edit"):
        raise BusinessError("ليس لديك صلاحية قفل أو فتح الفترات")
    with session_scope() as s:
        row = s.scalar(select(FiscalPeriod).where(FiscalPeriod.year == year, FiscalPeriod.month == month))
        if row is None:
            row = FiscalPeriod(year=year, month=month)
            s.add(row)
        row.is_closed = closed
        audit.log(s, actor, "close_period" if closed else "open_period", "fiscal_period", f"{year}-{month:02d}")
