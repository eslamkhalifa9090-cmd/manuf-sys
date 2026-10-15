"""Integrity checks: the books against their subledgers and the month/year-end routines.

Each check returns a status (ok / warn / error), a one-line result and, when something is off,
a table of the offending rows so the accountant can follow it up.
"""
from dataclasses import dataclass, field
from datetime import date

import pandas as pd
from sqlalchemy import func, select

from core.db import session_scope
from core.models import Account, JournalEntry, JournalLine
from core.models.assets import YearClose
from core.models.documents import APPROVED, PENDING, RETURNED
from core.models.ledger import ENTRY_POSTED
from core.models.production import OverheadClose
from core.services import approval, fixed_assets, molds, parties, payroll, stock
from core.services.approval import r2
from core.services.settings import get_settings
from core.services.statements import TREASURY_PREFIX, _net, month_end

OK, WARN, ERROR = "ok", "warn", "error"
ICONS = {OK: "✅", WARN: "⚠️", ERROR: "❌"}
TOLERANCE = 0.01


@dataclass
class Check:
    title: str
    status: str
    result: str
    detail: pd.DataFrame = field(default_factory=pd.DataFrame)


def _gl(session, key: str, as_of: date) -> float:
    acc = session.scalar(select(Account).where(Account.system_key == key))
    return r2(_net(session, date_to=as_of).get(acc.id, 0.0)) if acc else 0.0


def trial_balance(as_of: date) -> Check:
    with session_scope() as s:
        dr, cr = s.execute(select(func.sum(JournalLine.debit), func.sum(JournalLine.credit))
                           .join(JournalEntry).where(JournalEntry.status == ENTRY_POSTED,
                                                     JournalEntry.entry_date <= as_of)).one()
        bad = []
        for e in s.scalars(select(JournalEntry).where(JournalEntry.status == ENTRY_POSTED,
                                                      JournalEntry.entry_date <= as_of)):
            d = r2(sum(float(l.debit) - float(l.credit) for l in e.lines))
            if abs(d) >= TOLERANCE:
                bad.append({"رقم القيد": e.entry_no, "التاريخ": e.entry_date, "الفرق": d})
    diff = r2(float(dr or 0) - float(cr or 0))
    if abs(diff) < TOLERANCE and not bad:
        return Check("توازن ميزان المراجعة", OK, f"المدين = الدائن = {float(dr or 0):,.2f}")
    return Check("توازن ميزان المراجعة", ERROR, f"فرق {diff:,.2f} بين المدين والدائن", pd.DataFrame(bad))


def subledgers(as_of: date) -> Check:
    df = parties.reconciliation(as_of)
    untagged = df["بدون عميل/مورد"].abs().sum() if len(df) else 0
    if untagged < TOLERANCE:
        return Check("العملاء والموردين مقابل الحسابات الإجمالية (1401 / 2101)", OK,
                     "كل الحركات عليها عميل أو مورد", df)
    return Check("العملاء والموردين مقابل الحسابات الإجمالية (1401 / 2101)", WARN,
                 f"حركات بقيمة {untagged:,.2f} بدون عميل أو مورد", df)


def _diff_check(title: str, df: pd.DataFrame, diff_cols: list[str], ok_text: str) -> Check:
    total = float(sum(df[c].abs().sum() for c in diff_cols)) if len(df) else 0.0
    if total < TOLERANCE:
        return Check(title, OK, ok_text, df)
    return Check(title, ERROR, f"فرق {total:,.2f} بين السجل والأستاذ العام", df)


def inventory(as_of: date) -> Check:
    return _diff_check("المخزون مقابل حسابات المخزون", stock.reconciliation(as_of), ["الفرق"],
                       "قيمة المخزون مطابقة لأرصدة الحسابات")


def mold_register(as_of: date) -> Check:
    return _diff_check("سجل الأسطمبات مقابل 1106 / 1206", molds.reconciliation(as_of), ["الفرق"],
                       "سجل الأسطمبات مطابق للأستاذ العام")


def asset_register(as_of: date) -> Check:
    return _diff_check("سجل الأصول الثابتة مقابل 11xx / 12xx", fixed_assets.reconciliation(as_of),
                       ["فرق التكلفة", "فرق المجمع"], "سجل الأصول مطابق للأستاذ العام")


def wip(as_of: date) -> Check:
    with session_scope() as s:
        bal = _gl(s, "wip", as_of)
    if abs(bal) < TOLERANCE:
        return Check("إنتاج تحت التشغيل (1303)", OK, "الرصيد صفر، كل التشغيلات اتقفلت على المخزون")
    return Check("إنتاج تحت التشغيل (1303)", WARN,
                 f"الرصيد {bal:,.2f}؛ المفروض يرجع صفر بعد كل تشغيلة وأمر إنتاج")


def pending_documents(as_of: date) -> Check:
    df = approval.documents_frame((PENDING, RETURNED), date_to=as_of)
    if not len(df):
        return Check("مستندات لسه ما اتعتمدتش", OK, "مفيش مستندات معلقة")
    return Check("مستندات لسه ما اتعتمدتش", WARN, f"{len(df)} مستند في انتظار الاعتماد أو مرتجع للتعديل",
                 df.drop(columns=["ref"]))


def treasury(as_of: date) -> Check:
    with session_scope() as s:
        net = _net(s, date_to=as_of)
        rows = [{"الحساب": f"{a.code} - {a.name_ar}", "الرصيد": r2(net.get(a.id, 0.0))}
                for a in s.scalars(select(Account).where(Account.code.startswith(TREASURY_PREFIX),
                                                         Account.is_postable).order_by(Account.code))
                if r2(net.get(a.id, 0.0)) <= -TOLERANCE]
    if not rows:
        return Check("أرصدة الخزينة والبنوك", OK, "مفيش حساب نقدية رصيده بالسالب")
    return Check("أرصدة الخزينة والبنوك", ERROR, f"{len(rows)} حساب نقدية رصيده بالسالب", pd.DataFrame(rows))


def overhead_months(as_of: date) -> Check:
    """Months with applied conversion (5301) but no approved month-end close."""
    with session_scope() as s:
        acc = s.scalar(select(Account).where(Account.system_key == "applied_conversion"))
        dates = s.scalars(select(JournalEntry.entry_date).join(JournalLine).where(
            JournalLine.account_id == acc.id, JournalEntry.status == ENTRY_POSTED,
            JournalEntry.source_type != "overhead_close", JournalEntry.entry_date <= as_of)).all()
        closed = {(y, m) for y, m in s.execute(select(OverheadClose.year, OverheadClose.month)
                                               .where(OverheadClose.status == APPROVED))}
    current = (as_of.year, as_of.month) if as_of < month_end(as_of) else None
    open_months = sorted({(d.year, d.month) for d in dates} - closed - ({current} if current else set()))
    if not open_months:
        return Check("إقفال الأعباء الشهري (5301)", OK, "كل الشهور المنتهية اتقفلت")
    df = pd.DataFrame([{"الشهر": f"{y}-{m:02d}"} for y, m in open_months])
    return Check("إقفال الأعباء الشهري (5301)", WARN, f"{len(open_months)} شهر عليه تحميل ولسه ما اتقفلش", df)


def employee_advances(as_of: date) -> Check:
    df = payroll.advance_balances(as_of)
    register = r2(df["الرصيد"].sum()) if len(df) else 0.0
    with session_scope() as s:
        gl = _gl(s, "employee_advances", as_of)
    diff = r2(gl - register)
    detail = pd.DataFrame([{"البند": "أرصدة سلف الموظفين", "القيمة": register},
                           {"البند": "رصيد حساب 1602", "القيمة": gl}, {"البند": "الفرق", "القيمة": diff}])
    if abs(diff) < TOLERANCE:
        return Check("سلف الموظفين مقابل 1602", OK, f"الأرصدة مطابقة ({register:,.2f})", detail)
    return Check("سلف الموظفين مقابل 1602", WARN,
                 f"فرق {diff:,.2f}؛ ممكن يكون رصيد افتتاحي أو قيد يدوي على 1602 من غير سلفة", detail)


def year_closes(as_of: date) -> Check:
    """Fiscal years that ended before as_of with income-statement activity and no approved close."""
    start_month = int(get_settings().fiscal_year_start_month or 1)
    with session_scope() as s:
        first = s.scalar(select(func.min(JournalEntry.entry_date)).where(JournalEntry.status == ENTRY_POSTED))
        closed = set(s.scalars(select(YearClose.year).where(YearClose.status == APPROVED)))
        pl_accounts = set(s.scalars(select(Account.id).where(Account.statement == "PL")))
        rows = []
        if first:
            y = first.year if first.month >= start_month else first.year - 1
            while True:
                d1, d2 = fixed_assets.fiscal_year(y, start_month)
                if d2 >= as_of:
                    break
                if y not in closed:
                    pl = _net(s, d1, d2, exclude_close=True)
                    if any(abs(v) >= TOLERANCE for a, v in pl.items() if a in pl_accounts):
                        rows.append({"السنة المالية": f"{d1:%Y-%m-%d} إلى {d2:%Y-%m-%d}"})
                y += 1
    if not rows:
        return Check("إقفال السنوات المالية السابقة", OK, "كل السنوات المنتهية اتقفلت")
    return Check("إقفال السنوات المالية السابقة", WARN, f"{len(rows)} سنة منتهية لسه ما اتقفلتش في 3301",
                 pd.DataFrame(rows))


CHECKS = [trial_balance, subledgers, inventory, mold_register, asset_register, wip, treasury,
          employee_advances, overhead_months, year_closes, pending_documents]


def run_all(as_of: date | None = None) -> list[Check]:
    as_of = as_of or date.today()
    out = []
    for fn in CHECKS:
        try:
            out.append(fn(as_of))
        except Exception as exc:  # one failing check must not hide the others
            out.append(Check(fn.__name__, ERROR, f"تعذر تشغيل الفحص: {exc}"))
    return out
