"""General ledger reports from posted journal entries: trial balance, account statement, journal book.

Voided entries (un-approved documents) are excluded everywhere.
Opening balances are cumulative from the first entry; the year-end closing of
income-statement accounts into retained earnings arrives with the financial statements.
"""
from datetime import date

import pandas as pd
from sqlalchemy import func, select

from core.db import session_scope
from core.models import Account, JournalEntry, JournalLine, User
from core.models.ledger import ENTRY_POSTED, ENTRY_VOID
from core.services.approval import r2
from core.services.errors import BusinessError

DETAIL = 0  # trial-balance level meaning "postable accounts only"


def _sums(session, date_from: date | None = None, date_to: date | None = None, before: date | None = None):
    """{account_id: (debit, credit)} over posted lines in the range."""
    q = (select(JournalLine.account_id, func.sum(JournalLine.debit), func.sum(JournalLine.credit))
         .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
         .where(JournalEntry.status == ENTRY_POSTED)
         .group_by(JournalLine.account_id))
    if before is not None:
        q = q.where(JournalEntry.entry_date < before)
    if date_from is not None:
        q = q.where(JournalEntry.entry_date >= date_from)
    if date_to is not None:
        q = q.where(JournalEntry.entry_date <= date_to)
    return {acc: (float(dr or 0), float(cr or 0)) for acc, dr, cr in session.execute(q)}


def _split(net: float) -> tuple[float, float]:
    net = r2(net)
    return (net, 0.0) if net >= 0 else (0.0, -net)


def trial_balance(date_from: date, date_to: date, level: int = DETAIL, hide_zero: bool = True) -> pd.DataFrame:
    """Opening, period movement and closing per account, rolled up to `level` (1-4) or postable detail."""
    if date_from > date_to:
        raise BusinessError("تاريخ البداية بعد تاريخ النهاية")
    with session_scope() as s:
        accounts = s.scalars(select(Account).order_by(Account.code)).all()
        opening = _sums(s, before=date_from)
        period = _sums(s, date_from=date_from, date_to=date_to)
        leaves = [a for a in accounts if a.is_postable]
        shown = leaves if level == DETAIL else [a for a in accounts if a.level == level]
        rows = []
        for acc in shown:
            members = [l for l in leaves if l.code.startswith(acc.code)] if not acc.is_postable else [acc]
            o_net = sum(opening.get(m.id, (0, 0))[0] - opening.get(m.id, (0, 0))[1] for m in members)
            p_dr = sum(period.get(m.id, (0, 0))[0] for m in members)
            p_cr = sum(period.get(m.id, (0, 0))[1] for m in members)
            o_dr, o_cr = _split(o_net)
            c_dr, c_cr = _split(o_net + p_dr - p_cr)
            if hide_zero and not any(r2(v) for v in (o_dr, o_cr, p_dr, p_cr)):
                continue
            rows.append({
                "account_id": acc.id, "الكود": acc.code, "اسم الحساب": acc.name_ar,
                "افتتاحي مدين": o_dr, "افتتاحي دائن": o_cr, "حركة مدين": r2(p_dr), "حركة دائن": r2(p_cr),
                "ختامي مدين": c_dr, "ختامي دائن": c_cr,
            })
    cols = ["account_id", "الكود", "اسم الحساب", "افتتاحي مدين", "افتتاحي دائن", "حركة مدين", "حركة دائن",
            "ختامي مدين", "ختامي دائن"]
    return pd.DataFrame(rows, columns=cols)


def tb_totals(df: pd.DataFrame) -> dict:
    return {c: r2(df[c].sum()) for c in df.columns[3:]}


def is_balanced(totals: dict) -> bool:
    return all(r2(totals[f"{k} مدين"]) == r2(totals[f"{k} دائن"]) for k in ("افتتاحي", "حركة", "ختامي"))


def account_statement(account_id: int, date_from: date, date_to: date) -> tuple[pd.DataFrame, dict]:
    """Lines with a running balance. A header account includes all accounts under it."""
    with session_scope() as s:
        acc = s.get(Account, account_id)
        if acc is None:
            raise BusinessError("الحساب غير موجود")
        ids = [a.id for a in s.scalars(select(Account).where(Account.code.startswith(acc.code), Account.is_postable))]
        names = {a.id: f"{a.code} - {a.name_ar}" for a in s.scalars(select(Account).where(Account.id.in_(ids)))}
        o_dr = o_cr = 0.0
        for dr, cr in (v for k, v in _sums(s, before=date_from).items() if k in ids):
            o_dr += dr
            o_cr += cr
        q = (select(JournalLine, JournalEntry)
             .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
             .where(JournalEntry.status == ENTRY_POSTED, JournalLine.account_id.in_(ids),
                    JournalEntry.entry_date >= date_from, JournalEntry.entry_date <= date_to)
             .order_by(JournalEntry.entry_date, JournalEntry.id, JournalLine.id))
        balance = r2(o_dr - o_cr)
        rows = [{"التاريخ": date_from, "رقم القيد": "", "المستند": "", "الحساب": "", "البيان": "رصيد أول المدة",
                 "مدين": 0.0, "دائن": 0.0, "الرصيد": balance}]
        t_dr = t_cr = 0.0
        for line, entry in s.execute(q):
            balance = r2(balance + line.debit - line.credit)
            t_dr += line.debit
            t_cr += line.credit
            rows.append({"التاريخ": entry.entry_date, "رقم القيد": entry.entry_no, "المستند": entry.source_no or "",
                         "الحساب": names.get(line.account_id, ""), "البيان": line.memo or entry.memo or "",
                         "مدين": line.debit, "دائن": line.credit, "الرصيد": balance})
        info = {"account": f"{acc.code} - {acc.name_ar}", "normal_side": acc.normal_side, "is_header": not acc.is_postable,
                "opening": r2(o_dr - o_cr), "debit": r2(t_dr), "credit": r2(t_cr), "closing": balance}
    return pd.DataFrame(rows), info


def journal_book(date_from: date, date_to: date, include_void: bool = False) -> pd.DataFrame:
    with session_scope() as s:
        names = dict(s.execute(select(User.id, User.full_name)).all())
        accounts = {a.id: f"{a.code} - {a.name_ar}" for a in s.scalars(select(Account))}
        statuses = (ENTRY_POSTED, ENTRY_VOID) if include_void else (ENTRY_POSTED,)
        q = (select(JournalEntry)
             .where(JournalEntry.entry_date >= date_from, JournalEntry.entry_date <= date_to,
                    JournalEntry.status.in_(statuses))
             .order_by(JournalEntry.entry_date, JournalEntry.id))
        rows = []
        for e in s.scalars(q):
            for line in e.lines:
                rows.append({
                    "التاريخ": e.entry_date, "رقم القيد": e.entry_no, "المستند": e.source_no or "",
                    "الحساب": accounts.get(line.account_id, ""), "مدين": line.debit, "دائن": line.credit,
                    "البيان": line.memo or e.memo or "", "اعتمده": names.get(e.posted_by_id, ""),
                    "الحالة": "ملغي" if e.status == ENTRY_VOID else "مرحّل",
                })
    return pd.DataFrame(rows, columns=["التاريخ", "رقم القيد", "المستند", "الحساب", "مدين", "دائن", "البيان",
                                       "اعتمده", "الحالة"])


def account_in_use(session, account_id: int) -> bool:
    """True when any journal line or document references the account (posted, void or pending)."""
    from core.models import CashVoucher, ItemGroup, ManualJournalLine, PurchaseInvoice, PurchaseLine, StockVoucher

    checks = [
        select(JournalLine.id).where(JournalLine.account_id == account_id),
        select(ManualJournalLine.id).where(ManualJournalLine.account_id == account_id),
        select(CashVoucher.id).where((CashVoucher.treasury_account_id == account_id)
                                     | (CashVoucher.counter_account_id == account_id)),
        select(PurchaseLine.id).where(PurchaseLine.account_id == account_id),
        select(PurchaseInvoice.id).where(PurchaseInvoice.treasury_account_id == account_id),
        select(StockVoucher.id).where(StockVoucher.counter_account_id == account_id),
        select(ItemGroup.id).where(ItemGroup.inventory_account_id == account_id),
    ]
    return any(session.scalar(q.limit(1)) is not None for q in checks)
