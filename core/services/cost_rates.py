"""Predetermined conversion and overhead rates, and the suggestions behind them.

Each component has a rate history; the rate on a date is the latest one that took
effect on or before it. Suggestions come from posted actual expenses in the general
ledger over a period, divided by the pieces produced in it (entered by hand until
production orders exist).
"""
from datetime import date

import pandas as pd
from dateutil.relativedelta import relativedelta
from sqlalchemy import func, or_, select

from core.db import session_scope
from core.models import Account, CostRate, JournalEntry, JournalLine, User
from core.models.costing import (BASES, CONVERSION, ENERGY, GA, MAINTENANCE, OTHER, PCT, PER_PIECE, RATE_COMPONENTS,
                                 SELLING, WAGES)
from core.models.ledger import ENTRY_POSTED
from core.services import audit
from core.services.approval import r2
from core.services.errors import BusinessError

SCREEN = "cost_rates"

# Actual expense accounts behind each component, by account code prefix.
COMPONENT_ACCOUNTS = {
    WAGES: ("5201", "5202"),
    ENERGY: ("5203", "5204", "5206"),
    MAINTENANCE: ("5205",),
    OTHER: ("5207", "5208"),
    GA: ("6",),
    SELLING: ("7",),
}


def rates_on(on_date: date | None = None) -> dict[str, dict]:
    """{component: {"basis", "value", "valid_from", "id"}} in force on a date."""
    on_date = on_date or date.today()
    out = {}
    with session_scope() as s:
        for comp in RATE_COMPONENTS:
            r = s.scalar(select(CostRate).where(CostRate.component == comp, CostRate.valid_from <= on_date)
                         .order_by(CostRate.valid_from.desc(), CostRate.id.desc()).limit(1))
            if r is not None:
                out[comp] = {"id": r.id, "basis": r.basis, "value": float(r.value), "valid_from": r.valid_from}
    return out


def save_rate(actor, component: str, value: float, valid_from: date, basis: str = PER_PIECE, notes: str = "") -> int:
    if not actor.can(SCREEN, "add"):
        raise BusinessError("ليس لديك صلاحية إضافة معدلات")
    if component not in RATE_COMPONENTS:
        raise BusinessError("بند التحميل غير معروف")
    if component in CONVERSION and basis != PER_PIECE:
        raise BusinessError("التكاليف التحويلية بتتحمل كمبلغ للقطعة")
    if basis not in BASES:
        raise BusinessError("أساس التحميل غير معروف")
    if valid_from is None:
        raise BusinessError("تاريخ السريان مطلوب")
    if float(value or 0) < 0:
        raise BusinessError("المعدل مينفعش يكون بالسالب")
    if basis == PCT and float(value) >= 100:
        raise BusinessError("النسبة لازم تكون أقل من 100%")
    with session_scope() as s:
        same = s.scalar(select(CostRate).where(CostRate.component == component, CostRate.valid_from == valid_from))
        r = same or CostRate(component=component, valid_from=valid_from)
        r.basis, r.value, r.notes, r.created_by_id = basis, float(value), (notes or "").strip() or None, actor.id
        s.add(r)
        s.flush()
        audit.log(s, actor, "update" if same else "create", "cost_rate", r.id,
                  {"component": component, "basis": basis, "value": float(value), "valid_from": str(valid_from)})
        return r.id


def delete_rate(actor, rate_id: int) -> None:
    if not actor.can(SCREEN, "delete"):
        raise BusinessError("ليس لديك صلاحية الحذف")
    with session_scope() as s:
        r = s.get(CostRate, rate_id)
        if r is None:
            return
        audit.log(s, actor, "delete", "cost_rate", r.id, {"component": r.component, "value": float(r.value)})
        s.delete(r)


def rates_frame() -> pd.DataFrame:
    current = {v["id"] for v in rates_on().values()}
    with session_scope() as s:
        users = {u.id: u.full_name for u in s.scalars(select(User))}
        rows = [{"id": r.id, "البند": RATE_COMPONENTS.get(r.component, r.component), "الأساس": BASES.get(r.basis, r.basis),
                 "المعدل": float(r.value), "ساري من": r.valid_from, "الحالي": r.id in current, "ملاحظات": r.notes or "",
                 "بواسطة": users.get(r.created_by_id, "")}
                for r in s.scalars(select(CostRate).order_by(CostRate.component, CostRate.valid_from.desc()))]
    return pd.DataFrame(rows, columns=["id", "البند", "الأساس", "المعدل", "ساري من", "الحالي", "ملاحظات", "بواسطة"])


def _account_ids(session, prefixes: tuple[str, ...]) -> list[int]:
    q = select(Account.id).where(Account.is_postable)
    return list(session.scalars(q.where(or_(*[Account.code.startswith(p) for p in prefixes]))))


def actual_expenses(date_from: date, date_to: date) -> dict[str, float]:
    """Posted net debits per component, plus cost of sales, over a period."""
    with session_scope() as s:
        out = {}
        for comp, prefixes in list(COMPONENT_ACCOUNTS.items()) + [("cogs", ("5101",))]:
            ids = _account_ids(s, prefixes)
            if not ids:
                out[comp] = 0.0
                continue
            q = (select(func.sum(JournalLine.debit - JournalLine.credit)).join(JournalEntry)
                 .where(JournalEntry.status == ENTRY_POSTED, JournalEntry.entry_date.between(date_from, date_to),
                        JournalLine.account_id.in_(ids)))
            out[comp] = r2(s.scalar(q) or 0)
        return out


def default_period(months: int = 3, today: date | None = None) -> tuple[date, date]:
    """The last full `months` calendar months before today."""
    today = today or date.today()
    end = today.replace(day=1) - relativedelta(days=1)
    start = (end.replace(day=1) - relativedelta(months=months - 1))
    return start, end


def suggestions(date_from: date, date_to: date, pieces: float) -> pd.DataFrame:
    """Suggested rate per component from actual expenses: per piece, and as % of production cost for overheads."""
    actual = actual_expenses(date_from, date_to)
    production = sum(actual[c] for c in CONVERSION) + actual["cogs"]
    rows = []
    for comp in RATE_COMPONENTS:
        amount = actual[comp]
        row = {"component": comp, "البند": RATE_COMPONENTS[comp], "الفعلي في الفترة": amount,
               "المقترح للقطعة": round(amount / pieces, 4) if pieces else None, "المقترح %": None}
        if comp in (GA, SELLING) and production:
            row["المقترح %"] = round(amount / production * 100, 2)
        rows.append(row)
    return pd.DataFrame(rows)
