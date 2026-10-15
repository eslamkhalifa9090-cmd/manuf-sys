"""Financial statements from the posted ledger: income statement, balance sheet, cash flow (direct).

The income statement leaves out the year-end closing entry, so a closed year still shows
its results; the balance sheet shows the unclosed profit as its own equity line.
"""
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, select

from core.db import session_scope
from core.models import Account, JournalEntry, JournalLine
from core.models.ledger import ENTRY_POSTED
from core.services.approval import r2
from core.services.errors import BusinessError

CLOSE_SOURCE = "year_close"
TREASURY_PREFIX = "15"


def _net(session, date_from: date | None = None, date_to: date | None = None,
         exclude_close: bool = False) -> dict[int, float]:
    """{account_id: debit - credit} over posted lines in the range."""
    q = (select(JournalLine.account_id, func.sum(JournalLine.debit - JournalLine.credit))
         .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
         .where(JournalEntry.status == ENTRY_POSTED).group_by(JournalLine.account_id))
    if date_from:
        q = q.where(JournalEntry.entry_date >= date_from)
    if date_to:
        q = q.where(JournalEntry.entry_date <= date_to)
    if exclude_close:
        q = q.where(JournalEntry.source_type != CLOSE_SOURCE)
    return {a: float(v or 0) for a, v in session.execute(q)}


def month_end(d: date) -> date:
    nxt = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
    return nxt - timedelta(days=1)


def fiscal_months(start: date, end: date) -> list[tuple[str, date, date]]:
    """(label, first day, last day) for each month from start to end."""
    out, d = [], date(start.year, start.month, 1)
    while d <= end:
        e = min(month_end(d), end)
        out.append((d.strftime("%Y-%m"), max(d, start), e))
        d = e + timedelta(days=1)
    return out


# ---------------------------------------------------------------- income statement

# (key, label, account-code prefixes, sign) — sign +1 means credit balances are positive.
PL_BLOCKS = [
    ("sales", "المبيعات", ("41",), 1),
    ("deductions", "مردودات وخصومات المبيعات", ("42",), 1),
    ("cogs", "تكلفة البضاعة المباعة", ("51",), 1),
    ("conversion", "فرق التحميل غير المقفل (فعلي 52 - محمّل 53)", ("52", "53"), 1),
    ("losses", "هالك وفروق وتكاليف إنتاج أخرى", ("54",), 1),
    ("ga", "المصروفات العمومية والإدارية", ("6",), 1),
    ("selling", "مصروفات البيع والتسويق", ("7",), 1),
    ("other_income", "إيرادات أخرى", ("43",), 1),
    ("other_expense", "مصروفات أخرى", ("8",), 1),
]
SUBTOTALS = {
    "deductions": ("net_sales", "صافي المبيعات", ("sales", "deductions")),
    "losses": ("gross", "مجمل الربح", ("sales", "deductions", "cogs", "conversion", "losses")),
    "selling": ("operating", "ربح التشغيل", ("sales", "deductions", "cogs", "conversion", "losses", "ga", "selling")),
    "other_expense": ("net", "صافي الربح (الخسارة)", tuple(b[0] for b in PL_BLOCKS)),
}


def income_statement(columns: list[tuple[str, date, date]], detail: bool = True) -> pd.DataFrame:
    """One value column per (label, from, to). Revenue positive, costs negative; rows carry a `kind`."""
    if not columns:
        raise BusinessError("اختار فترة")
    with session_scope() as s:
        accounts = [a for a in s.scalars(select(Account).where(Account.statement == "PL").order_by(Account.code))
                    if a.is_postable]
        nets = [_net(s, d1, d2, exclude_close=True) for _, d1, d2 in columns]
    labels = [c[0] for c in columns]
    rows, block_totals = [], {}
    for key, label, prefixes, _ in PL_BLOCKS:
        members = [a for a in accounts if a.code.startswith(prefixes)]
        totals = [r2(-sum(n.get(a.id, 0.0) for a in members)) for n in nets]
        block_totals[key] = totals
        optional = key in ("conversion", "losses", "deductions", "other_income", "other_expense")
        if any(totals) or not optional:
            rows.append({"البند": label, "kind": "block"} | dict(zip(labels, totals)))
            if detail and key != "conversion":
                for a in members:
                    vals = [r2(-n.get(a.id, 0.0)) for n in nets]
                    if any(vals):
                        rows.append({"البند": f"    {a.code} - {a.name_ar}", "kind": "account"} | dict(zip(labels, vals)))
        if key in SUBTOTALS:
            sk, slabel, parts = SUBTOTALS[key]
            vals = [r2(sum(block_totals.get(p, [0] * len(labels))[i] for p in parts)) for i in range(len(labels))]
            block_totals[sk] = vals
            rows.append({"البند": slabel, "kind": "subtotal"} | dict(zip(labels, vals)))
            if sk == "gross":
                ns = block_totals["net_sales"]
                rows.append({"البند": "% مجمل الربح من صافي المبيعات", "kind": "pct"}
                            | {l: round(v / ns[i] * 100, 2) if ns[i] > 0 else None for i, (l, v) in enumerate(zip(labels, vals))})
    return pd.DataFrame(rows, columns=["البند", "kind"] + labels)


def net_profit(date_from: date, date_to: date) -> float:
    df = income_statement([("x", date_from, date_to)], detail=False)
    return float(df.loc[df["البند"] == SUBTOTALS["other_expense"][1], "x"].iloc[0])


# ---------------------------------------------------------------- balance sheet

BS_SECTIONS = [
    ("الأصول", "1", 1),
    ("الالتزامات", "2", -1),
    ("حقوق الملكية", "3", -1),
]


def balance_sheet(dates: list[date], detail: bool = True) -> pd.DataFrame:
    """Balances at each date by group (level 2) and account; profit not yet closed shows under equity."""
    if not dates:
        raise BusinessError("اختار تاريخ")
    labels = [d.isoformat() for d in dates]
    with session_scope() as s:
        accounts = s.scalars(select(Account).order_by(Account.code)).all()
        balances = [_net(s, date_to=d) for d in dates]
    leaves = [a for a in accounts if a.is_postable]
    pl_leaves = [a for a in leaves if a.statement == "PL"]
    rows, section_totals = [], {}
    for title, prefix, sign in BS_SECTIONS:
        rows.append({"البند": title, "kind": "section"} | {l: None for l in labels})
        groups = [a for a in accounts if a.level == 2 and a.code.startswith(prefix)]
        total = [0.0] * len(dates)
        for g in groups:
            members = [a for a in leaves if a.code.startswith(g.code)]
            vals = [r2(sign * sum(b.get(a.id, 0.0) for a in members)) for b in balances]
            if not any(vals):
                continue
            rows.append({"البند": f"  {g.name_ar}", "kind": "block"} | dict(zip(labels, vals)))
            if detail:
                for a in members:
                    av = [r2(sign * b.get(a.id, 0.0)) for b in balances]
                    if any(av):
                        rows.append({"البند": f"      {a.code} - {a.name_ar}", "kind": "account"} | dict(zip(labels, av)))
            total = [t + v for t, v in zip(total, vals)]
        if prefix == "3":
            profit = [r2(-sum(b.get(a.id, 0.0) for a in pl_leaves)) for b in balances]
            if any(profit):
                rows.append({"البند": "  أرباح (خسائر) لم تقفل بعد", "kind": "block"} | dict(zip(labels, profit)))
                total = [t + p for t, p in zip(total, profit)]
        section_totals[prefix] = [r2(t) for t in total]
        rows.append({"البند": f"إجمالي {title}", "kind": "subtotal"} | dict(zip(labels, section_totals[prefix])))
    rows.append({"البند": "إجمالي الالتزامات وحقوق الملكية", "kind": "subtotal"}
                | {l: r2(section_totals["2"][i] + section_totals["3"][i]) for i, l in enumerate(labels)})
    rows.append({"البند": "الفرق (لازم يكون صفر)", "kind": "check"}
                | {l: r2(section_totals["1"][i] - section_totals["2"][i] - section_totals["3"][i])
                   for i, l in enumerate(labels)})
    return pd.DataFrame(rows, columns=["البند", "kind"] + labels)


# ---------------------------------------------------------------- cash flow (direct)

INVESTING = ("11",)
FINANCING = ("31", "32", "33", "2204")
# Stock and its cost move together inside a sale; they never carry the cash.
NON_CASH_FIRST = ("13", "51", "12")
ACTIVITIES = {"operating": "الأنشطة التشغيلية", "investing": "الأنشطة الاستثمارية", "financing": "الأنشطة التمويلية"}


def _activity(code: str) -> str:
    if code.startswith(INVESTING):
        return "investing"
    if code.startswith(FINANCING):
        return "financing"
    return "operating"


def cash_flow(date_from: date, date_to: date) -> tuple[pd.DataFrame, dict]:
    """Cash in and out of the treasury accounts (15xx) by the account on the other side of each entry."""
    if date_from > date_to:
        raise BusinessError("تاريخ البداية بعد تاريخ النهاية")
    with session_scope() as s:
        accounts = {a.id: a for a in s.scalars(select(Account))}
        cash_ids = {i for i, a in accounts.items() if a.is_postable and a.code.startswith(TREASURY_PREFIX)}
        opening = r2(sum(v for a, v in _net(s, date_to=date_from - timedelta(days=1)).items() if a in cash_ids))
        q = (select(JournalLine.entry_id, JournalLine.account_id, JournalLine.debit, JournalLine.credit)
             .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
             .where(JournalEntry.status == ENTRY_POSTED, JournalEntry.entry_date >= date_from,
                    JournalEntry.entry_date <= date_to,
                    JournalEntry.id.in_(select(JournalLine.entry_id).where(JournalLine.account_id.in_(cash_ids)))))
        entries: dict[int, dict[int, float]] = {}
        for eid, acc, dr, cr in s.execute(q):
            e = entries.setdefault(eid, {})
            e[acc] = e.get(acc, 0.0) + float(dr or 0) - float(cr or 0)
    flows: dict[int, float] = {}
    for lines in entries.values():
        cash = sum(v for a, v in lines.items() if a in cash_ids)
        if abs(cash) < 0.005:
            continue  # a transfer between cash accounts
        # The cash came from the lines on the other side of it.
        others = {a: v for a, v in lines.items() if a not in cash_ids and v * cash < 0}
        preferred = {a: v for a, v in others.items() if not accounts[a].code.startswith(NON_CASH_FIRST)}
        pool = preferred or others
        base = sum(abs(v) for v in pool.values())
        for a, v in pool.items():
            flows[a] = flows.get(a, 0.0) + cash * abs(v) / base
    rows = []
    totals = {}
    for key, title in ACTIVITIES.items():
        items = sorted(((accounts[a], r2(v)) for a, v in flows.items() if _activity(accounts[a].code) == key and r2(v)),
                       key=lambda x: x[0].code)
        cash_in = r2(sum(v for _, v in items if v > 0))
        cash_out = r2(sum(v for _, v in items if v < 0))
        rows.append({"البند": title, "kind": "section", "المبلغ": None})
        for a, v in items:
            rows.append({"البند": f"    {a.code} - {a.name_ar}", "kind": "account", "المبلغ": v})
        totals[key] = r2(cash_in + cash_out)
        rows.append({"البند": f"صافي التدفق من {title}", "kind": "subtotal", "المبلغ": totals[key]})
    change = r2(sum(totals.values()))
    with session_scope() as s:
        closing = r2(sum(v for a, v in _net(s, date_to=date_to).items() if a in cash_ids))
    rows += [{"البند": "صافي التغير في النقدية", "kind": "subtotal", "المبلغ": change},
             {"البند": "النقدية أول الفترة", "kind": "block", "المبلغ": opening},
             {"البند": "النقدية آخر الفترة", "kind": "subtotal", "المبلغ": r2(opening + change)}]
    summary = {"opening": opening, "change": change, "closing": closing, "matches": r2(opening + change) == closing}
    return pd.DataFrame(rows, columns=["البند", "kind", "المبلغ"]), summary


def cash_by_account(as_of: date) -> pd.DataFrame:
    with session_scope() as s:
        net = _net(s, date_to=as_of)
        rows = [{"الحساب": f"{a.code} - {a.name_ar}", "الرصيد": r2(net.get(a.id, 0.0))}
                for a in s.scalars(select(Account).where(Account.code.startswith(TREASURY_PREFIX), Account.is_postable)
                                   .order_by(Account.code))]
    return pd.DataFrame(rows, columns=["الحساب", "الرصيد"])


def balances_by_key(keys: tuple[str, ...], as_of: date) -> dict[str, float]:
    """Debit-positive balances of system-keyed accounts at a date."""
    with session_scope() as s:
        net = _net(s, date_to=as_of)
        return {a.system_key: r2(net.get(a.id, 0.0))
                for a in s.scalars(select(Account).where(Account.system_key.in_(keys)))}


def monthly_results(date_from: date, date_to: date) -> pd.DataFrame:
    """Net sales, gross profit and net profit per month, for the dashboard chart."""
    cols = fiscal_months(date_from, date_to)
    df = income_statement(cols, detail=False).set_index("البند")
    labels = [c[0] for c in cols]
    pick = {"صافي المبيعات": SUBTOTALS["deductions"][1], "مجمل الربح": SUBTOTALS["losses"][1],
            "صافي الربح": SUBTOTALS["other_expense"][1]}
    return pd.DataFrame({k: [float(df.loc[v, m]) for m in labels] for k, v in pick.items()}, index=labels)
