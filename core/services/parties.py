"""Customers and suppliers, and their subsidiary ledger on the control accounts."""
from datetime import date

import pandas as pd
from sqlalchemy import func, select

from core.db import session_scope
from core.models import Account, JournalEntry, JournalLine, Party
from core.models.ledger import ENTRY_POSTED
from core.models.parties import BOTH, CUSTOMER, PARTY_KINDS, SUPPLIER
from core.services import audit
from core.services.approval import r2
from core.services.errors import BusinessError

SCREEN = "parties"
IMPORT_COLUMNS = ["اسم العميل / المورد", "النوع", "التليفون", "العنوان", "ملاحظات"]
KIND_BY_LABEL = {v: k for k, v in PARTY_KINDS.items()}


def _next_code(session, kind: str) -> str:
    prefix = "S" if kind == SUPPLIER else "C"
    n = session.scalar(select(func.count(Party.id)).where(Party.code.startswith(prefix + "-"))) or 0
    while True:
        n += 1
        code = f"{prefix}-{n:04d}"
        if session.scalar(select(Party.id).where(Party.code == code)) is None:
            return code


def options(kind: str | None = None, include_inactive: bool = False) -> dict[int, str]:
    """{id: name} for select boxes. kind='customer' also returns parties that are both."""
    with session_scope() as s:
        q = select(Party).order_by(Party.name)
        if kind:
            q = q.where(Party.kind.in_((kind, BOTH)))
        if not include_inactive:
            q = q.where(Party.is_active)
        return {p.id: p.name for p in s.scalars(q)}


def save_party(actor, party_id: int | None, name: str, kind: str, phone: str = "", address: str = "",
               notes: str = "", is_active: bool = True) -> int:
    name = (name or "").strip()
    if not name:
        raise BusinessError("الاسم مطلوب")
    if kind not in PARTY_KINDS:
        raise BusinessError("النوع غير معروف")
    with session_scope() as s:
        dup = s.scalar(select(Party.id).where(func.lower(Party.name) == name.lower()))
        if dup and dup != party_id:
            raise BusinessError(f"الاسم «{name}» مسجل قبل كده")
        if party_id is None:
            if not actor.can(SCREEN, "add"):
                raise BusinessError("ليس لديك صلاحية الإضافة")
            p = Party(code=_next_code(s, kind))
            s.add(p)
        else:
            if not actor.can(SCREEN, "edit"):
                raise BusinessError("ليس لديك صلاحية التعديل")
            p = s.get(Party, party_id)
            if p is None:
                raise BusinessError("غير موجود")
            if p.kind != kind and kind != BOTH and _has_postings(s, p.id):
                raise BusinessError("عليه حركات؛ ممكن تغيّر النوع لـ «عميل ومورد» بس")
        p.name, p.kind = name, kind
        p.phone, p.address = (phone or "").strip() or None, (address or "").strip() or None
        p.notes, p.is_active = (notes or "").strip() or None, is_active
        s.flush()
        audit.log(s, actor, "create" if party_id is None else "update", "party", p.id, {"name": name})
        return p.id


def _has_postings(session, party_id: int) -> bool:
    from core.models import CashVoucher, ManualJournalLine, PurchaseInvoice, StockVoucher
    from core.models.costing import PriceQuote
    from core.models.molds import Mold, MoldMaintenance, MoldSettlement

    checks = [select(JournalLine.id).where(JournalLine.party_id == party_id),
              select(ManualJournalLine.id).where(ManualJournalLine.party_id == party_id),
              select(CashVoucher.id).where(CashVoucher.party_id == party_id),
              select(PurchaseInvoice.id).where(PurchaseInvoice.party_id == party_id),
              select(StockVoucher.id).where(StockVoucher.party_id == party_id),
              select(PriceQuote.id).where(PriceQuote.party_id == party_id),
              select(Mold.id).where((Mold.supplier_id == party_id) | (Mold.customer_id == party_id)),
              select(MoldMaintenance.id).where(MoldMaintenance.party_id == party_id),
              select(MoldSettlement.id).where(MoldSettlement.party_id == party_id)]
    return any(session.scalar(q.limit(1)) is not None for q in checks)


def delete_party(actor, party_id: int) -> None:
    if not actor.can(SCREEN, "delete"):
        raise BusinessError("ليس لديك صلاحية الحذف")
    with session_scope() as s:
        p = s.get(Party, party_id)
        if p is None:
            return
        if _has_postings(s, p.id):
            raise BusinessError("عليه حركات؛ أوقفه بدلاً من الحذف")
        audit.log(s, actor, "delete", "party", p.id, {"name": p.name})
        s.delete(p)


def parties_frame(kind: str | None = None) -> pd.DataFrame:
    balances = balances_by_party()
    with session_scope() as s:
        q = select(Party).order_by(Party.code)
        if kind:
            q = q.where(Party.kind.in_((kind, BOTH)))
        rows = [{"id": p.id, "الكود": p.code, "الاسم": p.name, "النوع": PARTY_KINDS[p.kind], "التليفون": p.phone or "",
                 "العنوان": p.address or "", "الرصيد": balances.get(p.id, 0.0), "نشط": p.is_active,
                 "ملاحظات": p.notes or ""} for p in s.scalars(q)]
    return pd.DataFrame(rows, columns=["id", "الكود", "الاسم", "النوع", "التليفون", "العنوان", "الرصيد", "نشط", "ملاحظات"])


# ---------- subsidiary ledger ----------

def _control_ids(session) -> list[int]:
    return list(session.scalars(select(Account.id).where(Account.system_key.in_(("ar", "ap")))))


def balances_by_party(as_of: date | None = None) -> dict[int, float]:
    """Net debit balance per party on the control accounts (customers positive, suppliers negative)."""
    with session_scope() as s:
        q = (select(JournalLine.party_id, func.sum(JournalLine.debit - JournalLine.credit))
             .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
             .where(JournalEntry.status == ENTRY_POSTED, JournalLine.party_id.is_not(None),
                    JournalLine.account_id.in_(_control_ids(s)))
             .group_by(JournalLine.party_id))
        if as_of:
            q = q.where(JournalEntry.entry_date <= as_of)
        return {pid: r2(v) for pid, v in s.execute(q)}


def balances_frame(kind: str, as_of: date) -> pd.DataFrame:
    """Customer or supplier balances; suppliers shown as amounts owed (credit positive)."""
    bal = balances_by_party(as_of)
    sign = 1 if kind == CUSTOMER else -1
    with session_scope() as s:
        rows = []
        for p in s.scalars(select(Party).where(Party.kind.in_((kind, BOTH))).order_by(Party.name)):
            v = r2(bal.get(p.id, 0.0) * sign)
            rows.append({"party_id": p.id, "الكود": p.code, "الاسم": p.name, "التليفون": p.phone or "", "الرصيد": v})
    df = pd.DataFrame(rows, columns=["party_id", "الكود", "الاسم", "التليفون", "الرصيد"])
    return df


def statement(party_id: int, date_from: date, date_to: date) -> tuple[pd.DataFrame, dict]:
    """Party statement on the control accounts with a running balance (debit positive)."""
    with session_scope() as s:
        p = s.get(Party, party_id)
        if p is None:
            raise BusinessError("غير موجود")
        ctl = _control_ids(s)
        base = (select(JournalLine, JournalEntry).join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                .where(JournalEntry.status == ENTRY_POSTED, JournalLine.party_id == party_id,
                       JournalLine.account_id.in_(ctl)))
        opening = 0.0
        for line, _ in s.execute(base.where(JournalEntry.entry_date < date_from)):
            opening += line.debit - line.credit
        balance = r2(opening)
        rows = [{"التاريخ": date_from, "المستند": "", "البيان": "رصيد أول المدة", "مدين": 0.0, "دائن": 0.0,
                 "الرصيد": balance}]
        t_dr = t_cr = 0.0
        q = base.where(JournalEntry.entry_date >= date_from, JournalEntry.entry_date <= date_to).order_by(
            JournalEntry.entry_date, JournalEntry.id, JournalLine.id)
        for line, entry in s.execute(q):
            balance = r2(balance + line.debit - line.credit)
            t_dr += line.debit
            t_cr += line.credit
            rows.append({"التاريخ": entry.entry_date, "المستند": entry.source_no or entry.entry_no,
                         "البيان": line.memo or entry.memo or "", "مدين": line.debit, "دائن": line.credit,
                         "الرصيد": balance})
        info = {"name": p.name, "kind": p.kind, "opening": r2(opening), "debit": r2(t_dr), "credit": r2(t_cr),
                "closing": balance}
    return pd.DataFrame(rows), info


def reconciliation(as_of: date) -> pd.DataFrame:
    """Control account balance vs. the sum of its subsidiary ledger."""
    with session_scope() as s:
        rows = []
        for key, label in (("ar", "العملاء"), ("ap", "الموردين")):
            acc = s.scalar(select(Account).where(Account.system_key == key))
            q = (select(func.sum(JournalLine.debit - JournalLine.credit), JournalLine.party_id.is_(None))
                 .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                 .where(JournalEntry.status == ENTRY_POSTED, JournalLine.account_id == acc.id,
                        JournalEntry.entry_date <= as_of)
                 .group_by(JournalLine.party_id.is_(None)))
            tagged = untagged = 0.0
            for total, is_null in s.execute(q):
                if is_null:
                    untagged = float(total or 0)
                else:
                    tagged = float(total or 0)
            rows.append({"الحساب": f"{acc.code} - {acc.name_ar}", "رصيد الحساب": r2(tagged + untagged),
                         "مجموع الأرصدة الفرعية": r2(tagged), "بدون عميل/مورد": r2(untagged)})
    return pd.DataFrame(rows)


# ---------- Excel import ----------

def import_template() -> pd.DataFrame:
    return pd.DataFrame(columns=IMPORT_COLUMNS)


def validate_import(df: pd.DataFrame) -> pd.DataFrame:
    if IMPORT_COLUMNS[0] not in df.columns:
        raise BusinessError("عمود الاسم ناقص في الملف")
    df = df.copy()
    for col in IMPORT_COLUMNS:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str).str.strip()
    with session_scope() as s:
        existing = {n.lower() for n in s.scalars(select(Party.name))}
    seen, status = set(), []
    for rec in df.to_dict("records"):
        name, kind = rec[IMPORT_COLUMNS[0]], rec[IMPORT_COLUMNS[1]] or PARTY_KINDS[CUSTOMER]
        if not name:
            status.append("الاسم مطلوب")
        elif name.lower() in existing or name.lower() in seen:
            status.append("الاسم موجود")
        elif kind not in KIND_BY_LABEL:
            status.append("النوع لازم يكون: " + " / ".join(PARTY_KINDS.values()))
        else:
            status.append("جاهز")
            seen.add(name.lower())
    df["الحالة"] = status
    return df


def import_parties(actor, df: pd.DataFrame) -> int:
    checked = validate_import(df)
    count = 0
    for rec in checked[checked["الحالة"] == "جاهز"].to_dict("records"):
        save_party(actor, None, rec[IMPORT_COLUMNS[0]], KIND_BY_LABEL[rec[IMPORT_COLUMNS[1]] or PARTY_KINDS[CUSTOMER]],
                   rec[IMPORT_COLUMNS[2]], rec[IMPORT_COLUMNS[3]], rec[IMPORT_COLUMNS[4]])
        count += 1
    return count
