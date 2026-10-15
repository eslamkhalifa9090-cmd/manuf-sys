"""Mold documents that go through approval: maintenance, monthly amortization, settlement.

Maintenance: routine Dr 5205 / overhaul Dr 1106 (adds cost and life), Cr cash or supplier.
Amortization: Dr 5209 / Cr 1206 per mold, units in the month x prospective rate.
Settlement: charge the customer Dr 1401 / Cr 4301, and/or retire the mold
(Dr 1206 accumulated, Dr 5401 book value, Cr 1106 cost).
"""
from datetime import date

import pandas as pd
from sqlalchemy import select

from core.db import session_scope
from core.models import Account, Party, User
from core.models.documents import APPROVED, PENDING, RETURNED
from core.models.molds import (ACTIVE, AGREED, AMORTIZED, COUNTER_UNITS, MAINTENANCE_KINDS, OVERHAUL, RETIRED, ROUTINE,
                               Mold, MoldAmortization, MoldAmortizationLine, MoldMaintenance, MoldSettlement,
                               MoldUsage)
from core.models.parties import BOTH, CUSTOMER, SUPPLIER
from core.services import approval, audit
from core.services import molds as reg
from core.services.approval import PostingLine, r2
from core.services.doc_rules import check_access, check_editable, own_only, resubmit, status_label
from core.services.errors import BusinessError
from core.services.periods import ensure_open

MAINT_SCREEN = "mold_maintenance"
AMORT_SCREEN = "mold_amortization"
SETTLE_SCREEN = "mold_settlement"
TREASURY_PREFIX = "15"


def _later_amortization(session, d: date, exclude_id: int | None = None):
    """An amortization document for a month after the month of `d`."""
    q = select(MoldAmortization).where((MoldAmortization.year > d.year)
                                       | ((MoldAmortization.year == d.year) & (MoldAmortization.month > d.month)))
    if exclude_id:
        q = q.where(MoldAmortization.id != exclude_id)
    return session.scalar(q.order_by(MoldAmortization.year, MoldAmortization.month).limit(1))


def _names(session):
    return dict(session.execute(select(User.id, User.full_name)).all())


# ---------------------------------------------------------------- maintenance

def save_maintenance(actor, doc_id: int | None, mold_id: int, doc_date: date, kind: str, amount: float,
                     counter_account_id: int, party_id: int | None = None, extra_life: float = 0,
                     memo: str = "") -> int:
    if kind not in MAINTENANCE_KINDS:
        raise BusinessError("اختار نوع الصيانة")
    if doc_date is None:
        raise BusinessError("التاريخ مطلوب")
    if float(amount or 0) <= 0:
        raise BusinessError("المبلغ لازم يكون أكبر من صفر")
    if float(extra_life or 0) < 0:
        raise BusinessError("العمر الإضافي مينفعش يكون بالسالب")
    with session_scope() as s:
        m = s.get(Mold, mold_id)
        if m is None:
            raise BusinessError("اختار الأسطمبة")
        if m.status != ACTIVE:
            raise BusinessError("الأسطمبة خارج الخدمة")
        if kind == OVERHAUL and m.ownership not in AMORTIZED:
            raise BusinessError("التعمير بيتضاف على تكلفة أسطمبات المصنع بس؛ أسطمبة العميل صيانتها مصروف")
        acc = s.get(Account, counter_account_id) if counter_account_id else None
        if acc is None or not acc.is_postable or not acc.is_active:
            raise BusinessError("اختار اتدفعت من فين (خزينة / بنك) أو المورد")
        if acc.system_key == "ap":
            p = s.get(Party, party_id) if party_id else None
            if p is None or p.kind not in (SUPPLIER, BOTH):
                raise BusinessError("اختار المورد")
        elif not acc.code.startswith(TREASURY_PREFIX):
            raise BusinessError("الحساب المقابل لازم يكون خزينة أو بنك أو الموردين")
        else:
            party_id = None
        if doc_id is None:
            check_access(actor, MAINT_SCREEN, "add")
            doc = MoldMaintenance(created_by_id=actor.id)
        else:
            doc = s.get(MoldMaintenance, doc_id)
            if doc is None:
                raise BusinessError("المستند غير موجود")
            check_access(actor, MAINT_SCREEN, "edit", doc)
            check_editable(doc)
            resubmit(doc)
        ensure_open(s, doc_date)
        doc.mold_id, doc.doc_date, doc.kind, doc.amount = m.id, doc_date, kind, r2(amount)
        doc.extra_life = float(extra_life or 0) if kind == OVERHAUL else 0
        doc.counter_account_id, doc.party_id = acc.id, party_id or None
        doc.memo = (memo or "").strip() or None
        s.add(doc)
        s.flush()
        audit.log(s, actor, "create" if doc_id is None else "update", "mold_maintenance", doc.id, {"doc_no": doc.doc_no})
        return doc.id


def delete_maintenance(actor, doc_id: int) -> None:
    with session_scope() as s:
        doc = s.get(MoldMaintenance, doc_id)
        if doc is None:
            return
        check_access(actor, MAINT_SCREEN, "delete", doc)
        check_editable(doc)
        audit.log(s, actor, "delete", "mold_maintenance", doc.id, {"doc_no": doc.doc_no})
        s.delete(doc)


def maintenance_frame(actor, mold_id: int | None = None) -> pd.DataFrame:
    with session_scope() as s:
        names, molds = _names(s), {m.id: m for m in s.scalars(select(Mold))}
        q = select(MoldMaintenance).order_by(MoldMaintenance.doc_date.desc(), MoldMaintenance.id.desc())
        if mold_id:
            q = q.where(MoldMaintenance.mold_id == mold_id)
        if own_only(actor, MAINT_SCREEN):
            q = q.where(MoldMaintenance.created_by_id == actor.id)
        rows = [{"id": d.id, "رقم المستند": d.doc_no, "التاريخ": d.doc_date,
                 "الأسطمبة": f"{molds[d.mold_id].code} - {molds[d.mold_id].name}", "النوع": MAINTENANCE_KINDS[d.kind],
                 "المبلغ": float(d.amount), "عمر إضافي": float(d.extra_life or 0), "البيان": d.memo or "",
                 "الحالة": status_label(d.status), "ملاحظة المراجع": d.rejection_note or "",
                 "سجّله": names.get(d.created_by_id, "")} for d in s.scalars(q)]
    return pd.DataFrame(rows, columns=["id", "رقم المستند", "التاريخ", "الأسطمبة", "النوع", "المبلغ", "عمر إضافي",
                                       "البيان", "الحالة", "ملاحظة المراجع", "سجّله"])


def get_maintenance(doc_id: int) -> dict:
    with session_scope() as s:
        d = s.get(MoldMaintenance, doc_id)
        if d is None:
            raise BusinessError("المستند غير موجود")
        return {c.name: getattr(d, c.name) for c in MoldMaintenance.__table__.columns} | {"doc_no": d.doc_no}


def _maint_lines(session, doc: MoldMaintenance) -> list[PostingLine]:
    m = session.get(Mold, doc.mold_id)
    memo = f"{MAINTENANCE_KINDS[doc.kind]} - {m.code} {m.name}" + (f" - {doc.memo}" if doc.memo else "")
    debit = reg.system_account(session, "mold_asset" if doc.kind == OVERHAUL else "factory_maintenance")
    return [PostingLine(debit, debit=doc.amount, memo=memo),
            PostingLine(doc.counter_account_id, credit=doc.amount, memo=memo, party_id=doc.party_id)]


def _maint_void(session, doc: MoldMaintenance) -> None:
    if doc.kind == OVERHAUL:
        later = _later_amortization(session, doc.doc_date)
        if later is not None or session.scalar(select(MoldAmortization.id).where(
                MoldAmortization.year == doc.doc_date.year, MoldAmortization.month == doc.doc_date.month)):
            raise BusinessError("اتحسب إهلاك بعد التعمير ده؛ الغي مستندات الإهلاك اللي بعده الأول")


def _maint_post(session, doc: MoldMaintenance, entry_id) -> None:
    if doc.kind == OVERHAUL:
        _maint_void(session, doc)


def _maint_describe(session, doc: MoldMaintenance):
    m = session.get(Mold, doc.mold_id)
    return "صيانة أسطمبة", f"{m.code} {m.name} - {MAINTENANCE_KINDS[doc.kind]}", float(doc.amount)


approval.register(approval.DocType("mold_maintenance", MoldMaintenance, MAINT_SCREEN, _maint_describe, _maint_lines,
                                   on_post=_maint_post, on_void=_maint_void))


# ---------------------------------------------------------------- amortization

def plan_month(session, year: int, month: int, exclude_doc_id: int | None = None) -> list[dict]:
    """Lines for a month: units run x (remaining depreciable amount / remaining life) per amortized mold."""
    start, end = date(year, month, 1), reg.month_end(year, month)
    out = []
    for m in session.scalars(select(Mold).where(Mold.ownership.in_(AMORTIZED), Mold.status == ACTIVE).order_by(Mold.code)):
        units = reg.usage_units(session, m.id, start, end)
        if units <= 0:
            continue
        cost = reg.cost_total(session, m, end)
        acc = reg.accumulated(session, m, statuses=(APPROVED, PENDING, RETURNED), before=(year, month))
        base = max(r2(cost - float(m.salvage_value or 0) - acc), 0.0)
        remaining = reg.life_total(session, m, end) - float(m.opening_usage or 0) - reg.usage_units(session, m.id,
                                                                                                    before=start)
        rate = round(base / remaining, 6) if remaining > 0 else 0.0
        amount = base if units >= remaining else min(r2(units * rate), base)
        out.append({"mold_id": m.id, "code": m.code, "name": m.name, "units": units, "rate": rate, "amount": r2(amount),
                    "unit": COUNTER_UNITS[m.counter_unit], "book_value_before": r2(cost - acc),
                    "remaining_before": round(remaining, 4)})
    return out


def preview_month(year: int, month: int) -> pd.DataFrame:
    """The lines compute_month would create, for review before computing."""
    with session_scope() as s:
        lines = plan_month(s, int(year), int(month))
    return pd.DataFrame([{"الأسطمبة": f"{l['code']} - {l['name']}", "التشغيل": l["units"], "الوحدة": l["unit"],
                          "صافي القيمة قبل الشهر": l["book_value_before"], "العمر الباقي قبل الشهر": l["remaining_before"],
                          "إهلاك الوحدة": l["rate"], "القيمة": l["amount"]} for l in lines],
                        columns=["الأسطمبة", "التشغيل", "الوحدة", "صافي القيمة قبل الشهر", "العمر الباقي قبل الشهر",
                                 "إهلاك الوحدة", "القيمة"])


def mold_amortization_history(mold_id: int) -> pd.DataFrame:
    with session_scope() as s:
        q = (select(MoldAmortization, MoldAmortizationLine).join(MoldAmortizationLine)
             .where(MoldAmortizationLine.mold_id == mold_id)
             .order_by(MoldAmortization.year.desc(), MoldAmortization.month.desc()))
        rows = [{"رقم المستند": d.doc_no, "الشهر": f"{d.month:02d}/{d.year}", "التشغيل": float(l.units),
                 "إهلاك الوحدة": float(l.rate), "القيمة": float(l.amount), "الحالة": status_label(d.status)}
                for d, l in s.execute(q)]
    return pd.DataFrame(rows, columns=["رقم المستند", "الشهر", "التشغيل", "إهلاك الوحدة", "القيمة", "الحالة"])


def compute_month(actor, year: int, month: int, memo: str = "") -> int:
    """Create or refresh the month's amortization document (pending)."""
    if not 1 <= int(month) <= 12:
        raise BusinessError("الشهر غير صحيح")
    end = reg.month_end(int(year), int(month))
    with session_scope() as s:
        ensure_open(s, end)
        doc = s.scalar(select(MoldAmortization).where(MoldAmortization.year == year, MoldAmortization.month == month))
        if doc is None:
            check_access(actor, AMORT_SCREEN, "add")
        else:
            check_access(actor, AMORT_SCREEN, "edit", doc)
            check_editable(doc)
        later = _later_amortization(s, end)
        if later is not None:
            raise BusinessError(f"فيه إهلاك محسوب لشهر بعده ({later.month}/{later.year}); احذفه الأول")
        # Every earlier month that had usage must already have its document, so rates stay sequential.
        first = s.scalar(select(MoldUsage.usage_date).join(Mold, Mold.id == MoldUsage.mold_id)
                         .where(Mold.ownership.in_(AMORTIZED), MoldUsage.usage_date < date(year, month, 1))
                         .order_by(MoldUsage.usage_date))
        if first is not None:
            y, mth = first.year, first.month
            while (y, mth) < (year, month):
                has_usage = s.scalar(select(MoldUsage.id).join(Mold, Mold.id == MoldUsage.mold_id).where(
                    Mold.ownership.in_(AMORTIZED), MoldUsage.usage_date >= date(y, mth, 1),
                    MoldUsage.usage_date <= reg.month_end(y, mth)).limit(1))
                has_doc = s.scalar(select(MoldAmortization.id).where(MoldAmortization.year == y,
                                                                     MoldAmortization.month == mth))
                if has_usage and not has_doc:
                    raise BusinessError(f"احسب إهلاك شهر {mth}/{y} الأول")
                y, mth = (y + 1, 1) if mth == 12 else (y, mth + 1)
        lines = plan_month(s, year, month)
        if not lines or not any(l["amount"] for l in lines):
            raise BusinessError("مفيش تشغيل على أسطمبات المصنع في الشهر ده، أو الأسطمبات مستهلكة بالكامل")
        if doc is None:
            doc = MoldAmortization(created_by_id=actor.id, year=year, month=month)
            s.add(doc)
        else:
            resubmit(doc)
        doc.doc_date, doc.memo = end, (memo or "").strip() or f"إهلاك الأسطمبات عن شهر {month}/{year}"
        doc.lines = [MoldAmortizationLine(mold_id=l["mold_id"], units=l["units"], rate=l["rate"], amount=l["amount"])
                     for l in lines if l["amount"]]
        s.flush()
        audit.log(s, actor, "compute", "mold_amortization", doc.id,
                  {"doc_no": doc.doc_no, "total": r2(sum(l.amount for l in doc.lines))})
        return doc.id


def delete_amortization(actor, doc_id: int) -> None:
    with session_scope() as s:
        doc = s.get(MoldAmortization, doc_id)
        if doc is None:
            return
        check_access(actor, AMORT_SCREEN, "delete", doc)
        check_editable(doc)
        if _later_amortization(s, doc.doc_date) is not None:
            raise BusinessError("فيه إهلاك محسوب لشهر بعده؛ احذفه الأول")
        audit.log(s, actor, "delete", "mold_amortization", doc.id, {"doc_no": doc.doc_no})
        s.delete(doc)


def amortizations_frame(actor) -> pd.DataFrame:
    with session_scope() as s:
        names = _names(s)
        q = select(MoldAmortization).order_by(MoldAmortization.year.desc(), MoldAmortization.month.desc())
        if own_only(actor, AMORT_SCREEN):
            q = q.where(MoldAmortization.created_by_id == actor.id)
        rows = [{"id": d.id, "رقم المستند": d.doc_no, "الشهر": f"{d.month:02d}/{d.year}", "التاريخ": d.doc_date,
                 "عدد الأسطمبات": len(d.lines), "القيمة": r2(sum(float(l.amount) for l in d.lines)),
                 "الحالة": status_label(d.status), "ملاحظة المراجع": d.rejection_note or "",
                 "سجّله": names.get(d.created_by_id, "")} for d in s.scalars(q)]
    return pd.DataFrame(rows, columns=["id", "رقم المستند", "الشهر", "التاريخ", "عدد الأسطمبات", "القيمة", "الحالة",
                                       "ملاحظة المراجع", "سجّله"])


def amortization_lines(doc_id: int) -> pd.DataFrame:
    with session_scope() as s:
        d = s.get(MoldAmortization, doc_id)
        molds = {m.id: m for m in s.scalars(select(Mold))}
        rows = [{"الأسطمبة": f"{molds[l.mold_id].code} - {molds[l.mold_id].name}", "الوحدات": float(l.units),
                 "الوحدة": COUNTER_UNITS[molds[l.mold_id].counter_unit], "إهلاك الوحدة": float(l.rate),
                 "القيمة": float(l.amount)} for l in d.lines]
    return pd.DataFrame(rows, columns=["الأسطمبة", "الوحدات", "الوحدة", "إهلاك الوحدة", "القيمة"])


def _amort_lines(session, doc: MoldAmortization) -> list[PostingLine]:
    exp, acc = reg.system_account(session, "mold_amortization"), reg.system_account(session, "mold_acc_amortization")
    out = []
    for l in doc.lines:
        m = session.get(Mold, l.mold_id)
        memo = f"إهلاك {m.code} {m.name} - {float(l.units):g} {COUNTER_UNITS[m.counter_unit]}"
        out += [PostingLine(exp, debit=l.amount, memo=memo), PostingLine(acc, credit=l.amount, memo=memo)]
    return out


def _amort_void(session, doc: MoldAmortization) -> None:
    if _later_amortization(session, doc.doc_date) is not None:
        raise BusinessError("فيه إهلاك لشهر بعده؛ الغي اعتماده أو احذفه الأول")
    retired = session.scalar(select(MoldSettlement.id).where(MoldSettlement.retire, MoldSettlement.status == APPROVED,
                                                             MoldSettlement.doc_date >= doc.doc_date).limit(1))
    if retired:
        raise BusinessError("فيه تسوية شطب أسطمبة معتمدة بعد الشهر ده؛ الغيها الأول")


def _amort_post(session, doc: MoldAmortization, entry_id) -> None:
    earlier = session.scalar(select(MoldAmortization).where(
        MoldAmortization.status != APPROVED, MoldAmortization.id != doc.id,
        (MoldAmortization.year < doc.year) | ((MoldAmortization.year == doc.year) & (MoldAmortization.month < doc.month)))
        .limit(1))
    if earlier is not None:
        raise BusinessError(f"اعتمد إهلاك شهر {earlier.month}/{earlier.year} الأول")


def _amort_describe(session, doc: MoldAmortization):
    return "إهلاك الأسطمبات", f"شهر {doc.month}/{doc.year} - {len(doc.lines)} أسطمبة", r2(sum(l.amount for l in doc.lines))


approval.register(approval.DocType("mold_amortization", MoldAmortization, AMORT_SCREEN, _amort_describe, _amort_lines,
                                   on_post=_amort_post, on_void=_amort_void))


# ---------------------------------------------------------------- settlement

def settlement_preview(mold_id: int, as_of: date | None = None) -> dict:
    """What a settlement would see: agreed vs used, remaining, cost, accumulated and book value."""
    as_of = as_of or date.today()
    with session_scope() as s:
        m = s.get(Mold, mold_id)
        if m is None:
            raise BusinessError("الأسطمبة غير موجودة")
        p = reg.position(s, m, as_of)
        agreed = float(m.agreed_qty or 0)
        return {"code": m.code, "name": m.name, "ownership": m.ownership, "unit": COUNTER_UNITS[m.counter_unit],
                "customer_id": m.customer_id, "agreed": agreed, "used": p["used"],
                "short": max(agreed - p["used"], 0.0) if m.ownership == AGREED else 0.0,
                "agreement_end": m.agreement_end, "cost": p["cost"], "accumulated": p["accumulated"],
                "book_value": p["book_value"] if m.ownership in AMORTIZED else 0.0, "recovered": p["recovered"]}


def save_settlement(actor, doc_id: int | None, mold_id: int, doc_date: date, recovery_amount: float = 0,
                    retire: bool = False, party_id: int | None = None, memo: str = "") -> int:
    if doc_date is None:
        raise BusinessError("التاريخ مطلوب")
    recovery = r2(recovery_amount or 0)
    if recovery < 0:
        raise BusinessError("مبلغ الاسترداد مينفعش يكون بالسالب")
    if not recovery and not retire:
        raise BusinessError("التسوية لازم يكون فيها مبلغ يتحمل على العميل أو شطب للأسطمبة")
    preview = settlement_preview(mold_id, doc_date)
    with session_scope() as s:
        m = s.get(Mold, mold_id)
        if m.status != ACTIVE:
            raise BusinessError("الأسطمبة خارج الخدمة بالفعل")
        party_id = party_id or m.customer_id
        if recovery:
            p = s.get(Party, party_id) if party_id else None
            if p is None or p.kind not in (CUSTOMER, BOTH):
                raise BusinessError("اختار العميل اللي هيتحمل مبلغ الاسترداد")
        if doc_id is None:
            check_access(actor, SETTLE_SCREEN, "add")
            doc = MoldSettlement(created_by_id=actor.id)
        else:
            doc = s.get(MoldSettlement, doc_id)
            if doc is None:
                raise BusinessError("المستند غير موجود")
            check_access(actor, SETTLE_SCREEN, "edit", doc)
            check_editable(doc)
            resubmit(doc)
        ensure_open(s, doc_date)
        doc.mold_id, doc.doc_date, doc.recovery_amount, doc.retire = m.id, doc_date, recovery, bool(retire)
        doc.party_id = party_id if recovery else None
        doc.agreed_qty, doc.used_units, doc.book_value = preview["agreed"], preview["used"], preview["book_value"]
        doc.memo = (memo or "").strip() or None
        s.add(doc)
        s.flush()
        audit.log(s, actor, "create" if doc_id is None else "update", "mold_settlement", doc.id, {"doc_no": doc.doc_no})
        return doc.id


def delete_settlement(actor, doc_id: int) -> None:
    with session_scope() as s:
        doc = s.get(MoldSettlement, doc_id)
        if doc is None:
            return
        check_access(actor, SETTLE_SCREEN, "delete", doc)
        check_editable(doc)
        audit.log(s, actor, "delete", "mold_settlement", doc.id, {"doc_no": doc.doc_no})
        s.delete(doc)


def settlements_frame(actor) -> pd.DataFrame:
    with session_scope() as s:
        names, molds = _names(s), {m.id: m for m in s.scalars(select(Mold))}
        parties = {p.id: p.name for p in s.scalars(select(Party))}
        q = select(MoldSettlement).order_by(MoldSettlement.doc_date.desc(), MoldSettlement.id.desc())
        if own_only(actor, SETTLE_SCREEN):
            q = q.where(MoldSettlement.created_by_id == actor.id)
        rows = [{"id": d.id, "رقم المستند": d.doc_no, "التاريخ": d.doc_date,
                 "الأسطمبة": f"{molds[d.mold_id].code} - {molds[d.mold_id].name}", "العميل": parties.get(d.party_id, ""),
                 "مبلغ الاسترداد": float(d.recovery_amount), "شطب": "نعم" if d.retire else "لا",
                 "صافي القيمة وقت التسجيل": float(d.book_value), "الحالة": status_label(d.status),
                 "ملاحظة المراجع": d.rejection_note or "", "سجّله": names.get(d.created_by_id, "")}
                for d in s.scalars(q)]
    return pd.DataFrame(rows, columns=["id", "رقم المستند", "التاريخ", "الأسطمبة", "العميل", "مبلغ الاسترداد", "شطب",
                                       "صافي القيمة وقت التسجيل", "الحالة", "ملاحظة المراجع", "سجّله"])


def get_settlement(doc_id: int) -> dict:
    with session_scope() as s:
        d = s.get(MoldSettlement, doc_id)
        if d is None:
            raise BusinessError("المستند غير موجود")
        return {c.name: getattr(d, c.name) for c in MoldSettlement.__table__.columns} | {"doc_no": d.doc_no}


def _settle_lines(session, doc: MoldSettlement) -> list[PostingLine]:
    m = session.get(Mold, doc.mold_id)
    memo = f"تسوية أسطمبة {m.code} {m.name}" + (f" - {doc.memo}" if doc.memo else "")
    lines = []
    if doc.recovery_amount:
        lines += [PostingLine(reg.system_account(session, "ar"), debit=doc.recovery_amount, memo=memo,
                              party_id=doc.party_id),
                  PostingLine(reg.system_account(session, "mold_recovery_income"), credit=doc.recovery_amount,
                              memo=memo)]
    if doc.retire and m.ownership in AMORTIZED:
        cost = reg.cost_total(session, m, doc.doc_date)
        acc = reg.accumulated(session, m, doc.doc_date)
        if cost:
            lines.append(PostingLine(reg.system_account(session, "mold_asset"), credit=cost, memo=memo))
            if acc:
                lines.append(PostingLine(reg.system_account(session, "mold_acc_amortization"), debit=acc, memo=memo))
            if r2(cost - acc) > 0:
                lines.append(PostingLine(reg.system_account(session, "mold_writeoff"), debit=r2(cost - acc),
                                         memo=memo))
    return lines


def _settle_post(session, doc: MoldSettlement, entry_id) -> None:
    if not doc.retire:
        return
    pending = session.scalar(select(MoldAmortization).where(MoldAmortization.status != APPROVED,
                                                            MoldAmortization.doc_date <= doc.doc_date).limit(1))
    if pending is not None:
        raise BusinessError(f"اعتمد إهلاك شهر {pending.month}/{pending.year} الأول قبل شطب الأسطمبة")
    if _later_amortization(session, doc.doc_date) is not None:
        raise BusinessError("فيه إهلاك محسوب لشهر بعد تاريخ الشطب؛ احذفه الأول")
    session.get(Mold, doc.mold_id).status = RETIRED


def _settle_void(session, doc: MoldSettlement) -> None:
    if doc.retire:
        session.get(Mold, doc.mold_id).status = ACTIVE


def _settle_describe(session, doc: MoldSettlement):
    m = session.get(Mold, doc.mold_id)
    what = " + ".join(x for x in ("استرداد من العميل" if doc.recovery_amount else "",
                                  "شطب" if doc.retire else "") if x)
    return "تسوية أسطمبة", f"{m.code} {m.name} - {what}", float(doc.recovery_amount or doc.book_value)


approval.register(approval.DocType("mold_settlement", MoldSettlement, SETTLE_SCREEN, _settle_describe, _settle_lines,
                                   on_post=_settle_post, on_void=_settle_void))
