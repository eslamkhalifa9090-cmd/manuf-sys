"""Mold register, usage counter, and the figures every mold document and report needs.

Cost = opening cost + approved purchase lines that name the mold + approved overhauls.
Life = useful life + extra life from approved overhauls (counter units).
Accumulated = opening amortization + amortization lines (approved, or also pending
where a calculation must see the months before it).
"""
import calendar
from datetime import date

import pandas as pd
from sqlalchemy import func, select

from core.db import session_scope
from core.models import (Account, Item, JournalEntry, JournalLine, Party, PriceQuote, PurchaseInvoice, PurchaseLine,
                         User)
from core.models.documents import APPROVED, PENDING, RETURNED
from core.models.inventory import PURCHASE_RETURN
from core.models.ledger import ENTRY_POSTED
from core.models.molds import (ACTIVE, AGREED, AMORTIZED, COUNTER_UNITS, CUSTOMER_OWNED, MOLD_STATUSES, OVERHAUL,
                               OWNERSHIP, PIECES_PER_COUNTER, RETIRED, Mold, MoldAmortization, MoldAmortizationLine,
                               MoldMaintenance, MoldSettlement, MoldUsage)
from core.models.parties import BOTH, CUSTOMER, SUPPLIER
from core.services import audit
from core.services.approval import r2
from core.services.errors import BusinessError
from core.services.periods import ensure_open

SCREEN = "molds"
USAGE_SCREEN = "mold_usage"
MAX_IMAGE = 2 * 1024 * 1024
ALL_DOCS = (APPROVED, PENDING, RETURNED)


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def system_account(session, key: str) -> int:
    acc_id = session.scalar(select(Account.id).where(Account.system_key == key))
    if acc_id is None:
        raise BusinessError(f"حساب النظام {key} غير موجود في شجرة الحسابات")
    return acc_id


# ---------------------------------------------------------------- figures

def purchased_cost(session, mold_id: int, as_of: date | None = None) -> float:
    """Net (after invoice discount) of approved purchase lines naming the mold; returns subtract."""
    from core.services.purchases import net_amounts

    q = (select(PurchaseInvoice).join(PurchaseLine, PurchaseLine.invoice_id == PurchaseInvoice.id)
         .where(PurchaseLine.mold_id == mold_id, PurchaseInvoice.status == APPROVED).distinct())
    if as_of:
        q = q.where(PurchaseInvoice.doc_date <= as_of)
    total = 0.0
    for inv in session.scalars(q):
        sign = -1 if inv.kind == PURCHASE_RETURN else 1
        for line, net in zip(inv.lines, net_amounts(inv.lines, inv.discount or 0)):
            if line.mold_id == mold_id:
                total += sign * net
    return r2(total)


def _overhauls(session, mold_id: int, as_of: date | None, statuses=(APPROVED,)):
    q = select(MoldMaintenance).where(MoldMaintenance.mold_id == mold_id, MoldMaintenance.kind == OVERHAUL,
                                      MoldMaintenance.status.in_(statuses))
    if as_of:
        q = q.where(MoldMaintenance.doc_date <= as_of)
    return list(session.scalars(q))


def cost_total(session, mold: Mold, as_of: date | None = None) -> float:
    return r2(float(mold.opening_cost or 0) + purchased_cost(session, mold.id, as_of)
              + sum(float(m.amount) for m in _overhauls(session, mold.id, as_of)))


def life_total(session, mold: Mold, as_of: date | None = None) -> float:
    return float(mold.useful_life or 0) + sum(float(m.extra_life or 0) for m in _overhauls(session, mold.id, as_of))


def usage_units(session, mold_id: int, date_from: date | None = None, date_to: date | None = None,
                before: date | None = None) -> float:
    q = select(func.sum(MoldUsage.units)).where(MoldUsage.mold_id == mold_id)
    if date_from:
        q = q.where(MoldUsage.usage_date >= date_from)
    if date_to:
        q = q.where(MoldUsage.usage_date <= date_to)
    if before:
        q = q.where(MoldUsage.usage_date < before)
    return round(float(session.scalar(q) or 0), 4)


def accumulated(session, mold: Mold, as_of: date | None = None, statuses=(APPROVED,),
                before: tuple[int, int] | None = None) -> float:
    """Opening amortization plus amortization lines up to a date (or before a (year, month))."""
    q = (select(func.sum(MoldAmortizationLine.amount)).join(MoldAmortization)
         .where(MoldAmortizationLine.mold_id == mold.id, MoldAmortization.status.in_(statuses)))
    if as_of:
        q = q.where(MoldAmortization.doc_date <= as_of)
    if before:
        y, m = before
        q = q.where((MoldAmortization.year < y) | ((MoldAmortization.year == y) & (MoldAmortization.month < m)))
    return r2(float(mold.opening_amortization or 0) + float(session.scalar(q) or 0))


def recovered(session, mold_id: int, as_of: date | None = None) -> float:
    q = select(func.sum(MoldSettlement.recovery_amount)).where(MoldSettlement.mold_id == mold_id,
                                                               MoldSettlement.status == APPROVED)
    if as_of:
        q = q.where(MoldSettlement.doc_date <= as_of)
    return r2(session.scalar(q) or 0)


def position(session, mold: Mold, as_of: date | None = None) -> dict:
    """Cost, accumulated, book value, life, units used and the current per-unit rate."""
    as_of = as_of or date.today()
    cost = cost_total(session, mold, as_of)
    acc = accumulated(session, mold, as_of) if mold.ownership in AMORTIZED else 0.0
    life = life_total(session, mold, as_of)
    used = float(mold.opening_usage or 0) + usage_units(session, mold.id, date_to=as_of)
    remaining = max(life - used, 0.0)
    base = max(cost - float(mold.salvage_value or 0) - acc, 0.0)
    return {"cost": cost, "accumulated": acc, "book_value": r2(cost - acc), "life": life, "used": round(used, 4),
            "remaining": round(remaining, 4), "used_pct": round(used / life * 100, 2) if life else 0.0,
            "rate": round(base / remaining, 6) if remaining else 0.0, "recovered": recovered(session, mold.id, as_of)}


# ---------------------------------------------------------------- register

def _next_code(session) -> str:
    n = session.scalar(select(func.count(Mold.id))) or 0
    while True:
        n += 1
        code = f"M-{n:04d}"
        if session.scalar(select(Mold.id).where(Mold.code == code)) is None:
            return code


def _has_activity(session, mold_id: int) -> bool:
    checks = [select(MoldUsage.id).where(MoldUsage.mold_id == mold_id),
              select(MoldMaintenance.id).where(MoldMaintenance.mold_id == mold_id),
              select(MoldAmortizationLine.id).where(MoldAmortizationLine.mold_id == mold_id),
              select(MoldSettlement.id).where(MoldSettlement.mold_id == mold_id)]
    return any(session.scalar(q.limit(1)) is not None for q in checks)


def save_mold(actor, mold_id: int | None, data: dict) -> int:
    """data keys: name, ownership, supplier_id, customer_id, purchase_date, cavities, counter_unit, useful_life,
    salvage_value, agreed_qty, agreement_end, opening_date, opening_cost, opening_amortization, opening_usage,
    products, notes, code, status, and optionally image / image_mime / clear_image."""
    name = (data.get("name") or "").strip()
    if not name:
        raise BusinessError("اسم الأسطمبة مطلوب")
    ownership = data.get("ownership")
    if ownership not in OWNERSHIP:
        raise BusinessError("اختار ملكية الأسطمبة")
    unit = data.get("counter_unit")
    if unit not in COUNTER_UNITS:
        raise BusinessError("اختار وحدة العداد")
    cavities = int(data.get("cavities") or 0)
    if cavities < 1:
        raise BusinessError("عدد التجاويف لازم يكون 1 أو أكتر")
    life = float(data.get("useful_life") or 0)
    nums = {k: float(data.get(k) or 0) for k in ("salvage_value", "agreed_qty", "opening_cost", "opening_amortization",
                                                 "opening_usage")}
    if life < 0 or any(v < 0 for v in nums.values()):
        raise BusinessError("مينفعش قيم بالسالب")
    if ownership in AMORTIZED and life <= 0:
        raise BusinessError("العمر الافتراضي بوحدة العداد مطلوب للأسطمبات اللي ملك المصنع")
    if ownership in (AGREED, CUSTOMER_OWNED) and not data.get("customer_id"):
        raise BusinessError("اختار العميل")
    if ownership == AGREED and (nums["agreed_qty"] <= 0 or not data.get("agreement_end")):
        raise BusinessError("اكتب الكمية المتفق عليها وتاريخ انتهاء الاتفاق")
    if nums["opening_amortization"] > nums["opening_cost"]:
        raise BusinessError("مجمع الاستهلاك الافتتاحي أكبر من التكلفة")
    if (nums["opening_cost"] or nums["opening_usage"] or nums["opening_amortization"]) and not data.get("opening_date"):
        raise BusinessError("اكتب تاريخ الرصيد الافتتاحي")
    if ownership in AMORTIZED and nums["opening_usage"] >= life:
        raise BusinessError("القطع المستهلكة قبل البداية لازم تكون أقل من العمر الافتراضي")
    if ownership == CUSTOMER_OWNED and (nums["opening_cost"] or nums["opening_amortization"]):
        raise BusinessError("أسطمبة ملك العميل ملهاش تكلفة ولا استهلاك عند المصنع")
    image = data.get("image")
    if image is not None and len(image) > MAX_IMAGE:
        raise BusinessError("الصورة أكبر من 2 ميجا")
    with session_scope() as s:
        dup = s.scalar(select(Mold.id).where(Mold.name == name))
        if dup and dup != mold_id:
            raise BusinessError("اسم الأسطمبة موجود")
        code = (data.get("code") or "").strip()
        if code:
            dup = s.scalar(select(Mold.id).where(Mold.code == code))
            if dup and dup != mold_id:
                raise BusinessError(f"الكود {code} مستخدم")
        for pid, kinds, label in ((data.get("supplier_id"), (SUPPLIER, BOTH), "المورد"),
                                  (data.get("customer_id"), (CUSTOMER, BOTH), "العميل")):
            if pid:
                p = s.get(Party, pid)
                if p is None or p.kind not in kinds:
                    raise BusinessError(f"{label} غير صحيح")
        if mold_id is None:
            if not actor.can(SCREEN, "add"):
                raise BusinessError("ليس لديك صلاحية الإضافة")
            m = Mold(code=code or _next_code(s))
            s.add(m)
        else:
            if not actor.can(SCREEN, "edit"):
                raise BusinessError("ليس لديك صلاحية التعديل")
            m = s.get(Mold, mold_id)
            if m is None:
                raise BusinessError("الأسطمبة غير موجودة")
            busy = _has_activity(s, m.id)
            if busy and m.counter_unit != unit:
                raise BusinessError("الأسطمبة عليها حركات؛ مينفعش تغيّر وحدة العداد")
            if busy and (m.ownership in AMORTIZED) != (ownership in AMORTIZED):
                raise BusinessError("الأسطمبة عليها حركات؛ مينفعش تحوّلها بين ملك المصنع وملك العميل")
            locked = s.scalar(select(MoldAmortizationLine.id).where(MoldAmortizationLine.mold_id == m.id).limit(1))
            changed = (float(m.useful_life or 0) != life or float(m.salvage_value or 0) != nums["salvage_value"]
                       or float(m.opening_cost or 0) != nums["opening_cost"]
                       or float(m.opening_amortization or 0) != nums["opening_amortization"]
                       or float(m.opening_usage or 0) != nums["opening_usage"])
            if locked and changed:
                raise BusinessError("اتحسب إهلاك على الأسطمبة؛ التكلفة والعمر والأرصدة الافتتاحية اتقفلت. "
                                    "التعمير بيتسجل من شاشة الصيانة والتعمير.")
            if code:
                m.code = code
        m.name, m.ownership, m.counter_unit, m.cavities, m.useful_life = name, ownership, unit, cavities, life
        m.supplier_id, m.customer_id = data.get("supplier_id") or None, data.get("customer_id") or None
        m.purchase_date, m.agreement_end = data.get("purchase_date"), data.get("agreement_end") if ownership == AGREED else None
        m.salvage_value, m.agreed_qty = nums["salvage_value"], nums["agreed_qty"] if ownership == AGREED else 0
        m.opening_date = data.get("opening_date")
        m.opening_cost, m.opening_amortization, m.opening_usage = (nums["opening_cost"], nums["opening_amortization"],
                                                                   nums["opening_usage"])
        m.products = (data.get("products") or "").strip() or None
        m.notes = (data.get("notes") or "").strip() or None
        if data.get("status") in MOLD_STATUSES and mold_id is not None:
            m.status = data["status"]
        if image is not None:
            m.image, m.image_mime = image, data.get("image_mime") or "image/png"
        elif data.get("clear_image"):
            m.image, m.image_mime = None, None
        s.flush()
        audit.log(s, actor, "create" if mold_id is None else "update", "mold", m.id, {"code": m.code, "name": name})
        return m.id


def delete_mold(actor, mold_id: int) -> None:
    if not actor.can(SCREEN, "delete"):
        raise BusinessError("ليس لديك صلاحية الحذف")
    with session_scope() as s:
        m = s.get(Mold, mold_id)
        if m is None:
            return
        refs = [select(PurchaseLine.id).where(PurchaseLine.mold_id == mold_id),
                select(PriceQuote.id).where(PriceQuote.mold_id == mold_id)]
        if _has_activity(s, mold_id) or any(s.scalar(q.limit(1)) is not None for q in refs):
            raise BusinessError("الأسطمبة عليها حركات أو مستندات؛ غيّر حالتها لـ «خارج الخدمة» بدل الحذف")
        audit.log(s, actor, "delete", "mold", m.id, {"code": m.code, "name": m.name})
        s.delete(m)


def mold_options(active_only: bool = True, ownership: tuple[str, ...] | None = None) -> dict[int, str]:
    with session_scope() as s:
        q = select(Mold).order_by(Mold.code)
        if active_only:
            q = q.where(Mold.status == ACTIVE)
        if ownership:
            q = q.where(Mold.ownership.in_(ownership))
        return {m.id: f"{m.code} - {m.name}" for m in s.scalars(q)}


def get_mold(mold_id: int) -> dict:
    with session_scope() as s:
        m = s.get(Mold, mold_id)
        if m is None:
            raise BusinessError("الأسطمبة غير موجودة")
        d = {c.name: getattr(m, c.name) for c in Mold.__table__.columns}
        for k in ("useful_life", "salvage_value", "agreed_qty", "opening_cost", "opening_amortization", "opening_usage"):
            d[k] = float(d[k] or 0)
        d["position"] = position(s, m)
        parties = {p.id: p.name for p in s.scalars(select(Party).where(Party.id.in_([m.supplier_id, m.customer_id])))}
        d["supplier_name"], d["customer_name"] = parties.get(m.supplier_id, ""), parties.get(m.customer_id, "")
        return d


def report_frame(as_of: date | None = None, warn_days: int = 30, warn_pct: float = 90) -> pd.DataFrame:
    """One row per mold: values, life, usage, recovery and a warning text."""
    as_of = as_of or date.today()
    with session_scope() as s:
        parties = {p.id: p.name for p in s.scalars(select(Party))}
        rows = []
        for m in s.scalars(select(Mold).order_by(Mold.code)):
            p = position(s, m, as_of)
            warn = []
            if m.status == ACTIVE and p["life"] and p["used_pct"] >= warn_pct:
                warn.append(f"استهلكت {p['used_pct']:g}% من عمرها")
            if m.ownership == AGREED and m.status == ACTIVE and m.agreement_end:
                left = (m.agreement_end - as_of).days
                short = float(m.agreed_qty or 0) - p["used"]
                if short > 0 and left <= warn_days:
                    warn.append(("الاتفاق انتهى" if left < 0 else f"الاتفاق بينتهي بعد {left} يوم")
                                + f" وباقي {short:,.0f} {COUNTER_UNITS[m.counter_unit]}")
            rows.append({
                "id": m.id, "الكود": m.code, "الأسطمبة": m.name, "الملكية": OWNERSHIP[m.ownership],
                "العميل": parties.get(m.customer_id, ""), "وحدة العداد": COUNTER_UNITS[m.counter_unit],
                "التجاويف": m.cavities, "التكلفة": p["cost"], "مجمع الاستهلاك": p["accumulated"],
                "صافي القيمة الدفترية": p["book_value"], "العمر الافتراضي": p["life"], "المستهلك": p["used"],
                "الباقي": p["remaining"], "% الاستهلاك": p["used_pct"], "إهلاك الوحدة الحالي": p["rate"],
                "المسترد من العملاء": p["recovered"], "الكمية المتفق عليها": float(m.agreed_qty or 0),
                "نهاية الاتفاق": m.agreement_end, "الحالة": MOLD_STATUSES[m.status], "تنبيه": " — ".join(warn),
            })
    cols = ["id", "الكود", "الأسطمبة", "الملكية", "العميل", "وحدة العداد", "التجاويف", "التكلفة", "مجمع الاستهلاك",
            "صافي القيمة الدفترية", "العمر الافتراضي", "المستهلك", "الباقي", "% الاستهلاك", "إهلاك الوحدة الحالي",
            "المسترد من العملاء", "الكمية المتفق عليها", "نهاية الاتفاق", "الحالة", "تنبيه"]
    return pd.DataFrame(rows, columns=cols)


def reconciliation(as_of: date | None = None) -> pd.DataFrame:
    """Register totals against the GL balances of 1106 (cost) and 1206 (accumulated)."""
    as_of = as_of or date.today()
    with session_scope() as s:
        molds = [m for m in s.scalars(select(Mold)) if m.ownership in AMORTIZED]
        reg_cost = r2(sum(cost_total(s, m, as_of) for m in molds))
        reg_acc = r2(sum(accumulated(s, m, as_of) for m in molds))
        # Retired molds left the books through their settlement.
        retired = [m for m in molds if m.status == RETIRED]
        for m in retired:
            settled = s.scalar(select(MoldSettlement.id).where(MoldSettlement.mold_id == m.id, MoldSettlement.retire,
                                                               MoldSettlement.status == APPROVED,
                                                               MoldSettlement.doc_date <= as_of).limit(1))
            if settled:
                reg_cost = r2(reg_cost - cost_total(s, m, as_of))
                reg_acc = r2(reg_acc - accumulated(s, m, as_of))

        def gl(key: str) -> float:
            acc_id = system_account(s, key)
            q = (select(func.sum(JournalLine.debit - JournalLine.credit)).join(JournalEntry)
                 .where(JournalLine.account_id == acc_id, JournalEntry.status == ENTRY_POSTED,
                        JournalEntry.entry_date <= as_of))
            return r2(s.scalar(q) or 0)

        gl_cost, gl_acc = gl("mold_asset"), -gl("mold_acc_amortization")
    return pd.DataFrame([
        {"البند": "تكلفة الأسطمبات (1106)", "سجل الأسطمبات": reg_cost, "الأستاذ العام": gl_cost,
         "الفرق": r2(reg_cost - gl_cost)},
        {"البند": "مجمع الاستهلاك (1206)", "سجل الأسطمبات": reg_acc, "الأستاذ العام": gl_acc,
         "الفرق": r2(reg_acc - gl_acc)},
    ])


# ---------------------------------------------------------------- usage

def _doc_from(session, d: date):
    """An amortization document for the month of `d` or any later month, if one exists."""
    return session.scalar(select(MoldAmortization).where(
        (MoldAmortization.year > d.year) | ((MoldAmortization.year == d.year) & (MoldAmortization.month >= d.month)))
        .order_by(MoldAmortization.year, MoldAmortization.month).limit(1))


def _check_usage_open(session, d: date) -> None:
    ensure_open(session, d)
    doc = _doc_from(session, d)
    if doc is not None:
        raise BusinessError(f"إهلاك شهر {doc.month}/{doc.year} اتحسب ({doc.doc_no})؛ "
                            "احذفه أو الغي اعتماده الأول عشان تعدّل التشغيل في الشهر ده أو قبله")


def counter_units(mold: Mold, shots: float, cavities: int) -> tuple[float, float]:
    """(pieces, units in the mold's counter unit) for a number of shots."""
    pieces = float(shots) * int(cavities)
    return pieces, round(pieces / PIECES_PER_COUNTER[mold.counter_unit], 4)


def save_usage(actor, usage_id: int | None, mold_id: int, usage_date: date, shots: float, cavities: int | None = None,
               item_id: int | None = None, memo: str = "") -> int:
    if usage_date is None:
        raise BusinessError("التاريخ مطلوب")
    if float(shots or 0) <= 0:
        raise BusinessError("عدد الكبسات لازم يكون أكبر من صفر")
    with session_scope() as s:
        m = s.get(Mold, mold_id)
        if m is None:
            raise BusinessError("اختار الأسطمبة")
        if m.status != ACTIVE:
            raise BusinessError("الأسطمبة خارج الخدمة")
        cav = int(cavities or m.cavities)
        if not 1 <= cav <= m.cavities:
            raise BusinessError(f"التجاويف الشغالة لازم تكون بين 1 و {m.cavities}")
        if m.opening_date and usage_date <= m.opening_date:
            raise BusinessError("التاريخ قبل تاريخ الرصيد الافتتاحي للأسطمبة")
        if item_id and s.get(Item, item_id) is None:
            raise BusinessError("الصنف غير موجود")
        _check_usage_open(s, usage_date)
        if usage_id is None:
            if not actor.can(USAGE_SCREEN, "add"):
                raise BusinessError("ليس لديك صلاحية الإضافة")
            u = MoldUsage(created_by_id=actor.id)
            s.add(u)
        else:
            if not actor.can(USAGE_SCREEN, "edit"):
                raise BusinessError("ليس لديك صلاحية التعديل")
            u = s.get(MoldUsage, usage_id)
            if u is None:
                raise BusinessError("الإذن غير موجود")
            _check_usage_open(s, u.usage_date)
        pieces, units = counter_units(m, shots, cav)
        u.mold_id, u.usage_date, u.shots, u.cavities, u.pieces, u.units = m.id, usage_date, float(shots), cav, pieces, units
        u.item_id, u.memo = item_id or None, (memo or "").strip() or None
        s.flush()
        audit.log(s, actor, "create" if usage_id is None else "update", "mold_usage", u.id,
                  {"mold": m.code, "shots": float(shots), "units": units})
        return u.id


def delete_usage(actor, usage_id: int) -> None:
    if not actor.can(USAGE_SCREEN, "delete"):
        raise BusinessError("ليس لديك صلاحية الحذف")
    with session_scope() as s:
        u = s.get(MoldUsage, usage_id)
        if u is None:
            return
        _check_usage_open(s, u.usage_date)
        audit.log(s, actor, "delete", "mold_usage", u.id, {"mold_id": u.mold_id, "units": float(u.units)})
        s.delete(u)


def usage_frame(mold_id: int | None = None, date_from: date | None = None, date_to: date | None = None) -> pd.DataFrame:
    with session_scope() as s:
        molds = {m.id: m for m in s.scalars(select(Mold))}
        items = {i.id: i.name for i in s.scalars(select(Item))}
        users = {u.id: u.full_name for u in s.scalars(select(User))}
        q = select(MoldUsage).order_by(MoldUsage.usage_date.desc(), MoldUsage.id.desc())
        if mold_id:
            q = q.where(MoldUsage.mold_id == mold_id)
        if date_from:
            q = q.where(MoldUsage.usage_date >= date_from)
        if date_to:
            q = q.where(MoldUsage.usage_date <= date_to)
        rows = [{"id": u.id, "التاريخ": u.usage_date, "الأسطمبة": f"{molds[u.mold_id].code} - {molds[u.mold_id].name}",
                 "الكبسات": float(u.shots), "التجاويف الشغالة": u.cavities, "القطع": float(u.pieces),
                 "بوحدة العداد": float(u.units), "الوحدة": COUNTER_UNITS[molds[u.mold_id].counter_unit],
                 "المنتج": items.get(u.item_id, ""), "البيان": u.memo or "", "سجّله": users.get(u.created_by_id, "")}
                for u in s.scalars(q)]
    return pd.DataFrame(rows, columns=["id", "التاريخ", "الأسطمبة", "الكبسات", "التجاويف الشغالة", "القطع",
                                       "بوحدة العداد", "الوحدة", "المنتج", "البيان", "سجّله"])


def quote_defaults(mold_id: int) -> dict:
    """What a quote takes from the register: name, cost, and life and remaining life in pieces."""
    with session_scope() as s:
        m = s.get(Mold, mold_id)
        p = position(s, m)
        per = PIECES_PER_COUNTER[m.counter_unit]
        return {"name": f"{m.code} - {m.name}", "cost": p["cost"], "life_pieces": p["life"] * per,
                "remaining_pieces": p["remaining"] * per, "book_value": p["book_value"], "ownership": m.ownership}
