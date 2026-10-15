"""Fixed-asset register, monthly straight-line depreciation, and the fiscal year-end close."""
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, select

from core.db import session_scope
from core.models import Account, JournalEntry, JournalLine, User
from core.models.assets import (ADMIN, ASSET_ACTIVE, ASSET_DEPARTMENTS, ASSET_DISPOSED, ASSET_STATUSES, CATEGORY_KEYS,
                                FACTORY, Depreciation, DepreciationLine, FixedAsset, YearClose)
from core.models.documents import APPROVED, PENDING, RETURNED
from core.models.ledger import ENTRY_POSTED
from core.services import approval, audit
from core.services.approval import PostingLine, r2
from core.services.doc_rules import check_access, check_editable, own_only, resubmit, status_label
from core.services.errors import BusinessError
from core.services.periods import ensure_open
from core.services.statements import CLOSE_SOURCE, month_end

SCREEN = "fixed_assets"
DEP_SCREEN = "depreciation"
CLOSE_SCREEN = "year_close"
ALL_DOCS = (APPROVED, PENDING, RETURNED)


def _key_account(session, key: str) -> Account:
    acc = session.scalar(select(Account).where(Account.system_key == key))
    if acc is None:
        raise BusinessError(f"حساب النظام {key} غير موجود في الشجرة")
    return acc


def category_options() -> dict[int, str]:
    """Asset accounts the register uses (molds have their own register)."""
    with session_scope() as s:
        rows = s.scalars(select(Account).where(Account.system_key.in_(list(CATEGORY_KEYS))).order_by(Account.code))
        return {a.id: f"{a.code} - {a.name_ar}" for a in rows}


def _acc_dep_account(session, asset_account_id: int) -> int:
    acc = session.get(Account, asset_account_id)
    return _key_account(session, CATEGORY_KEYS[acc.system_key]).id


def _names(session):
    return dict(session.execute(select(User.id, User.full_name)).all())


def _f(x) -> float:
    try:
        v = float(x or 0)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v != v else v


# ---------------------------------------------------------------- register

def _next_code(session) -> str:
    n = session.scalar(select(func.count(FixedAsset.id))) or 0
    while True:
        n += 1
        code = f"FA-{n:04d}"
        if session.scalar(select(FixedAsset.id).where(FixedAsset.code == code)) is None:
            return code


def _depreciated(session, asset_id: int) -> bool:
    return session.scalar(select(DepreciationLine.id).where(DepreciationLine.asset_id == asset_id).limit(1)) is not None


def save_asset(actor, asset_id: int | None, data: dict) -> int:
    name = (data.get("name") or "").strip()
    if not name:
        raise BusinessError("اسم الأصل مطلوب")
    cost, salvage = r2(_f(data.get("cost"))), r2(_f(data.get("salvage_value")))
    life = int(_f(data.get("life_months")))
    opening_acc = r2(_f(data.get("opening_accumulated")))
    if cost <= 0:
        raise BusinessError("التكلفة لازم تكون أكبر من صفر")
    if life <= 0:
        raise BusinessError("العمر الإنتاجي بالشهور لازم يكون أكبر من صفر")
    if salvage < 0 or salvage >= cost:
        raise BusinessError("قيمة الخردة لازم تكون أقل من التكلفة")
    if opening_acc < 0 or opening_acc > cost - salvage:
        raise BusinessError("الإهلاك الافتتاحي أكبر من القيمة القابلة للإهلاك")
    acquired, start = data.get("acquisition_date"), data.get("depreciation_start") or data.get("acquisition_date")
    if acquired is None:
        raise BusinessError("تاريخ الشراء مطلوب")
    if (start.year, start.month) < (acquired.year, acquired.month):
        raise BusinessError("بداية الإهلاك قبل شهر الشراء")
    opening_date = data.get("opening_date") or None
    if opening_acc and opening_date is None:
        raise BusinessError("حدد تاريخ الإهلاك الافتتاحي")
    department = data.get("department") or FACTORY
    if department not in ASSET_DEPARTMENTS:
        raise BusinessError("القسم غير معروف")
    status = data.get("status") or ASSET_ACTIVE
    disposal = data.get("disposal_date") if status == ASSET_DISPOSED else None
    if status == ASSET_DISPOSED and disposal is None:
        raise BusinessError("حدد تاريخ الاستبعاد")
    with session_scope() as s:
        if data.get("account_id") not in category_options():
            raise BusinessError("اختار فئة الأصل")
        if asset_id is None:
            if not actor.can(SCREEN, "add"):
                raise BusinessError("ليس لديك صلاحية الإضافة")
            a = FixedAsset(created_by_id=actor.id, code=(data.get("code") or "").strip() or _next_code(s))
        else:
            if not actor.can(SCREEN, "edit"):
                raise BusinessError("ليس لديك صلاحية التعديل")
            a = s.get(FixedAsset, asset_id)
            if a is None:
                raise BusinessError("الأصل غير موجود")
            if _depreciated(s, a.id):
                locked = (float(a.cost) != cost or float(a.salvage_value or 0) != salvage or a.life_months != life
                          or float(a.opening_accumulated or 0) != opening_acc or a.account_id != data["account_id"]
                          or a.depreciation_start != start or a.department != department)
                if locked:
                    raise BusinessError("اتحسب إهلاك على الأصل؛ التكلفة والعمر والفئة والقسم اتقفلوا")
            if data.get("code"):
                a.code = data["code"].strip()
        dup = s.scalar(select(FixedAsset.id).where(FixedAsset.code == a.code, FixedAsset.id != (asset_id or 0)))
        if dup:
            raise BusinessError("كود الأصل مستخدم قبل كده")
        a.name, a.account_id, a.department = name, data["account_id"], department
        a.acquisition_date, a.depreciation_start = acquired, start
        a.cost, a.salvage_value, a.life_months = cost, salvage, life
        a.opening_date, a.opening_accumulated = opening_date, opening_acc
        a.status, a.disposal_date = status, disposal
        a.notes = (data.get("notes") or "").strip() or None
        s.add(a)
        s.flush()
        audit.log(s, actor, "create" if asset_id is None else "update", "fixed_asset", a.id,
                  {"code": a.code, "name": a.name, "cost": cost})
        return a.id


def delete_asset(actor, asset_id: int) -> None:
    if not actor.can(SCREEN, "delete"):
        raise BusinessError("ليس لديك صلاحية الحذف")
    with session_scope() as s:
        a = s.get(FixedAsset, asset_id)
        if a is None:
            return
        if _depreciated(s, a.id):
            raise BusinessError("الأصل عليه إهلاك؛ استبعده بدل الحذف")
        audit.log(s, actor, "delete", "fixed_asset", a.id, {"code": a.code, "name": a.name})
        s.delete(a)


def get_asset(asset_id: int) -> dict:
    with session_scope() as s:
        a = s.get(FixedAsset, asset_id)
        if a is None:
            raise BusinessError("الأصل غير موجود")
        return {c: getattr(a, c) for c in ("id", "code", "name", "account_id", "department", "acquisition_date",
                                           "depreciation_start", "life_months", "opening_date", "status",
                                           "disposal_date")} | {
            "cost": float(a.cost), "salvage_value": float(a.salvage_value or 0),
            "opening_accumulated": float(a.opening_accumulated or 0), "notes": a.notes or ""}


def accumulated(session, a: FixedAsset, before: tuple[int, int] | None = None, as_of: date | None = None,
                statuses=(APPROVED,)) -> float:
    q = (select(func.sum(DepreciationLine.amount)).join(Depreciation)
         .where(DepreciationLine.asset_id == a.id, Depreciation.status.in_(statuses)))
    if before:
        y, m = before
        q = q.where((Depreciation.year < y) | ((Depreciation.year == y) & (Depreciation.month < m)))
    if as_of:
        q = q.where(Depreciation.doc_date <= as_of)
    return r2(float(a.opening_accumulated or 0) + float(session.scalar(q) or 0))


def monthly_amount(a: FixedAsset) -> float:
    return r2((float(a.cost) - float(a.salvage_value or 0)) / a.life_months)


def register_frame(as_of: date | None = None) -> pd.DataFrame:
    as_of = as_of or date.today()
    with session_scope() as s:
        cats = {a.id: a.name_ar for a in s.scalars(select(Account))}
        rows = []
        for a in s.scalars(select(FixedAsset).order_by(FixedAsset.code)):
            acc = accumulated(s, a, as_of=as_of)
            rows.append({"id": a.id, "الكود": a.code, "الأصل": a.name, "الفئة": cats.get(a.account_id, ""),
                         "القسم": ASSET_DEPARTMENTS[a.department], "تاريخ الشراء": a.acquisition_date,
                         "التكلفة": float(a.cost), "الخردة": float(a.salvage_value or 0),
                         "العمر (شهور)": a.life_months, "إهلاك الشهر": monthly_amount(a), "مجمع الإهلاك": acc,
                         "صافي القيمة": r2(float(a.cost) - acc), "الحالة": ASSET_STATUSES[a.status]})
    return pd.DataFrame(rows, columns=["id", "الكود", "الأصل", "الفئة", "القسم", "تاريخ الشراء", "التكلفة", "الخردة",
                                       "العمر (شهور)", "إهلاك الشهر", "مجمع الإهلاك", "صافي القيمة", "الحالة"])


def reconciliation(as_of: date | None = None) -> pd.DataFrame:
    """Register cost and accumulated depreciation per category against the GL."""
    as_of = as_of or date.today()
    with session_scope() as s:
        gl = dict(s.execute(select(JournalLine.account_id, func.sum(JournalLine.debit - JournalLine.credit))
                            .join(JournalEntry).where(JournalEntry.status == ENTRY_POSTED,
                                                      JournalEntry.entry_date <= as_of)
                            .group_by(JournalLine.account_id)).all())
        assets = list(s.scalars(select(FixedAsset)))
        rows = []
        for key, dep_key in CATEGORY_KEYS.items():
            cat, dep = _key_account(s, key), _key_account(s, dep_key)
            mine = [a for a in assets if a.account_id == cat.id
                    and not (a.status == ASSET_DISPOSED and a.disposal_date and a.disposal_date <= as_of)]
            reg_cost = r2(sum(float(a.cost) for a in mine if a.acquisition_date <= as_of))
            reg_acc = r2(sum(accumulated(s, a, as_of=as_of) for a in mine))
            gl_cost, gl_acc = r2(gl.get(cat.id, 0) or 0), r2(-(gl.get(dep.id, 0) or 0))
            if reg_cost or gl_cost or reg_acc or gl_acc:
                rows.append({"الفئة": f"{cat.code} - {cat.name_ar}", "تكلفة السجل": reg_cost, "رصيد الحساب": gl_cost,
                             "فرق التكلفة": r2(gl_cost - reg_cost), "مجمع السجل": reg_acc,
                             "رصيد المجمع": gl_acc, "فرق المجمع": r2(gl_acc - reg_acc)})
    return pd.DataFrame(rows, columns=["الفئة", "تكلفة السجل", "رصيد الحساب", "فرق التكلفة", "مجمع السجل",
                                       "رصيد المجمع", "فرق المجمع"])


# ---------------------------------------------------------------- monthly depreciation

def plan_month(session, year: int, month: int) -> list[dict]:
    start, end = date(year, month, 1), month_end(date(year, month, 1))
    out = []
    for a in session.scalars(select(FixedAsset).order_by(FixedAsset.code)):
        if a.status == ASSET_DISPOSED and a.disposal_date and a.disposal_date < start:
            continue
        first = date(a.depreciation_start.year, a.depreciation_start.month, 1)
        if a.opening_date:  # the opening figure covers up to the end of its month
            first = max(first, month_end(a.opening_date) + timedelta(days=1))
        if start < first:
            continue
        acc = accumulated(session, a, before=(year, month), statuses=ALL_DOCS)
        remaining = r2(float(a.cost) - float(a.salvage_value or 0) - acc)
        if remaining <= 0:
            continue
        amount = min(monthly_amount(a), remaining)
        out.append({"asset_id": a.id, "code": a.code, "name": a.name, "department": a.department, "amount": r2(amount),
                    "accumulated_before": acc, "book_value_before": r2(float(a.cost) - acc), "end": end})
    return out


def preview_month(year: int, month: int) -> pd.DataFrame:
    with session_scope() as s:
        lines = plan_month(s, int(year), int(month))
    return pd.DataFrame([{"الأصل": f"{l['code']} - {l['name']}", "القسم": ASSET_DEPARTMENTS[l["department"]],
                          "صافي القيمة قبل الشهر": l["book_value_before"], "إهلاك الشهر": l["amount"]} for l in lines],
                        columns=["الأصل", "القسم", "صافي القيمة قبل الشهر", "إهلاك الشهر"])


def _ym(d) -> tuple[int, int]:
    return d.year, d.month


def compute_month(actor, year: int, month: int, memo: str = "") -> int:
    year, month = int(year), int(month)
    if not 1 <= month <= 12:
        raise BusinessError("الشهر غير صحيح")
    end = month_end(date(year, month, 1))
    with session_scope() as s:
        ensure_open(s, end)
        doc = s.scalar(select(Depreciation).where(Depreciation.year == year, Depreciation.month == month))
        if doc is None:
            check_access(actor, DEP_SCREEN, "add")
        else:
            check_access(actor, DEP_SCREEN, "edit", doc)
            check_editable(doc)
        later = s.scalar(select(Depreciation).where((Depreciation.year > year)
                                                    | ((Depreciation.year == year) & (Depreciation.month > month))).limit(1))
        if later is not None:
            raise BusinessError(f"فيه إهلاك محسوب لشهر بعده ({later.month}/{later.year}); احذفه الأول")
        last = s.scalar(select(Depreciation).where((Depreciation.year < year)
                                                   | ((Depreciation.year == year) & (Depreciation.month < month)))
                        .order_by(Depreciation.year.desc(), Depreciation.month.desc()).limit(1))
        if last is not None:
            expected = (last.year + 1, 1) if last.month == 12 else (last.year, last.month + 1)
            if expected != (year, month):
                raise BusinessError(f"احسب إهلاك شهر {expected[1]}/{expected[0]} الأول")
        lines = [l for l in plan_month(s, year, month) if l["amount"]]
        if not lines:
            raise BusinessError("مفيش أصول عليها إهلاك في الشهر ده")
        if doc is None:
            doc = Depreciation(created_by_id=actor.id, year=year, month=month)
            s.add(doc)
        else:
            resubmit(doc)
        doc.doc_date, doc.memo = end, (memo or "").strip() or f"إهلاك الأصول الثابتة عن شهر {month}/{year}"
        doc.lines = [DepreciationLine(asset_id=l["asset_id"], amount=l["amount"]) for l in lines]
        s.flush()
        audit.log(s, actor, "compute", "depreciation", doc.id,
                  {"doc_no": doc.doc_no, "total": r2(sum(l["amount"] for l in lines))})
        return doc.id


def delete_depreciation(actor, doc_id: int) -> None:
    with session_scope() as s:
        doc = s.get(Depreciation, doc_id)
        if doc is None:
            return
        check_access(actor, DEP_SCREEN, "delete", doc)
        check_editable(doc)
        later = s.scalar(select(Depreciation.id).where(
            (Depreciation.year > doc.year) | ((Depreciation.year == doc.year) & (Depreciation.month > doc.month))).limit(1))
        if later:
            raise BusinessError("فيه إهلاك محسوب لشهر بعده؛ احذفه الأول")
        audit.log(s, actor, "delete", "depreciation", doc.id, {"doc_no": doc.doc_no})
        s.delete(doc)


def depreciations_frame(actor) -> pd.DataFrame:
    with session_scope() as s:
        names = _names(s)
        q = select(Depreciation).order_by(Depreciation.year.desc(), Depreciation.month.desc())
        if own_only(actor, DEP_SCREEN):
            q = q.where(Depreciation.created_by_id == actor.id)
        rows = [{"id": d.id, "رقم المستند": d.doc_no, "الشهر": f"{d.month:02d}/{d.year}", "عدد الأصول": len(d.lines),
                 "القيمة": r2(sum(float(l.amount) for l in d.lines)), "الحالة": status_label(d.status),
                 "ملاحظة المراجع": d.rejection_note or "", "سجّله": names.get(d.created_by_id, "")}
                for d in s.scalars(q)]
    return pd.DataFrame(rows, columns=["id", "رقم المستند", "الشهر", "عدد الأصول", "القيمة", "الحالة", "ملاحظة المراجع",
                                       "سجّله"])


def depreciation_lines(doc_id: int) -> pd.DataFrame:
    with session_scope() as s:
        d = s.get(Depreciation, doc_id)
        assets = {a.id: a for a in s.scalars(select(FixedAsset))}
        rows = [{"الأصل": f"{assets[l.asset_id].code} - {assets[l.asset_id].name}",
                 "القسم": ASSET_DEPARTMENTS[assets[l.asset_id].department], "القيمة": float(l.amount)} for l in d.lines]
    return pd.DataFrame(rows, columns=["الأصل", "القسم", "القيمة"])


def _dep_lines(session, doc: Depreciation) -> list[PostingLine]:
    expense = {FACTORY: _key_account(session, "dep_factory").id, ADMIN: _key_account(session, "dep_admin").id}
    out = []
    for l in doc.lines:
        a = session.get(FixedAsset, l.asset_id)
        memo = f"إهلاك {a.code} {a.name} - {doc.month}/{doc.year}"
        out += [PostingLine(expense[a.department], debit=float(l.amount), memo=memo),
                PostingLine(_acc_dep_account(session, a.account_id), credit=float(l.amount), memo=memo)]
    return out


def _dep_post(session, doc: Depreciation, entry_id) -> None:
    earlier = session.scalar(select(Depreciation).where(
        Depreciation.status != APPROVED, Depreciation.id != doc.id,
        (Depreciation.year < doc.year) | ((Depreciation.year == doc.year) & (Depreciation.month < doc.month))).limit(1))
    if earlier is not None:
        raise BusinessError(f"اعتمد إهلاك شهر {earlier.month}/{earlier.year} الأول")


def _dep_void(session, doc: Depreciation) -> None:
    later = session.scalar(select(Depreciation.id).where(
        Depreciation.status == APPROVED,
        (Depreciation.year > doc.year) | ((Depreciation.year == doc.year) & (Depreciation.month > doc.month))).limit(1))
    if later:
        raise BusinessError("فيه إهلاك معتمد لشهر بعده؛ الغي اعتماده الأول")


def _dep_describe(session, doc: Depreciation):
    return "إهلاك الأصول الثابتة", f"شهر {doc.month}/{doc.year} - {len(doc.lines)} أصل", r2(
        sum(float(l.amount) for l in doc.lines))


approval.register(approval.DocType("depreciation", Depreciation, DEP_SCREEN, _dep_describe, _dep_lines,
                                   on_post=_dep_post, on_void=_dep_void))


# ---------------------------------------------------------------- year-end close

def fiscal_year(start_year: int, start_month: int) -> tuple[date, date]:
    d1 = date(start_year, start_month, 1)
    d2 = (date(start_year + 1, start_month, 1) if start_month > 1 else date(start_year + 1, 1, 1)) - timedelta(days=1)
    return d1, d2


def _pl_balances(session, date_to: date) -> list[tuple[Account, float]]:
    net = dict(session.execute(select(JournalLine.account_id, func.sum(JournalLine.debit - JournalLine.credit))
                               .join(JournalEntry).where(JournalEntry.status == ENTRY_POSTED,
                                                         JournalEntry.entry_date <= date_to)
                               .group_by(JournalLine.account_id)).all())
    accs = session.scalars(select(Account).where(Account.statement == "PL", Account.is_postable)
                                    .order_by(Account.code))
    return [(a, r2(net.get(a.id) or 0)) for a in accs if r2(net.get(a.id) or 0)]


def close_preview(start_year: int, start_month: int) -> dict:
    d1, d2 = fiscal_year(int(start_year), int(start_month))
    with session_scope() as s:
        bal = _pl_balances(s, d2)
        pending = approval.documents_frame((PENDING, RETURNED), d1, d2)
    profit = r2(-sum(v for _, v in bal))
    return {"date_from": d1, "date_to": d2, "profit": profit, "pending": len(pending),
            "lines": pd.DataFrame([{"الحساب": f"{a.code} - {a.name_ar}", "الرصيد": -v} for a, v in bal],
                                  columns=["الحساب", "الرصيد"])}


def compute_close(actor, start_year: int, start_month: int, memo: str = "") -> int:
    d1, d2 = fiscal_year(int(start_year), int(start_month))
    with session_scope() as s:
        ensure_open(s, d2)
        if not _pl_balances(s, d2):
            raise BusinessError("مفيش أرصدة إيرادات ومصروفات تتقفل")
        doc = s.scalar(select(YearClose).where(YearClose.year == int(start_year)))
        if doc is None:
            check_access(actor, CLOSE_SCREEN, "add")
            doc = YearClose(created_by_id=actor.id, year=int(start_year))
            s.add(doc)
        else:
            check_access(actor, CLOSE_SCREEN, "edit", doc)
            check_editable(doc)
            resubmit(doc)
        doc.date_from, doc.doc_date = d1, d2
        doc.memo = (memo or "").strip() or f"إقفال حسابات النتيجة عن السنة المالية {d1} إلى {d2}"
        s.flush()
        audit.log(s, actor, "compute", "year_close", doc.id, {"doc_no": doc.doc_no})
        return doc.id


def delete_close(actor, doc_id: int) -> None:
    with session_scope() as s:
        doc = s.get(YearClose, doc_id)
        if doc is None:
            return
        check_access(actor, CLOSE_SCREEN, "delete", doc)
        check_editable(doc)
        audit.log(s, actor, "delete", "year_close", doc.id, {"doc_no": doc.doc_no})
        s.delete(doc)


def closes_frame(actor) -> pd.DataFrame:
    with session_scope() as s:
        names = _names(s)
        rows = []
        for d in s.scalars(select(YearClose).order_by(YearClose.year.desc())):
            profit = None
            if d.status == APPROVED and d.journal_entry_id:
                re_acc = _key_account(s, "retained_earnings").id
                profit = r2(s.scalar(select(func.sum(JournalLine.credit - JournalLine.debit)).where(
                    JournalLine.entry_id == d.journal_entry_id, JournalLine.account_id == re_acc)) or 0)
            rows.append({"id": d.id, "رقم المستند": d.doc_no, "السنة المالية": f"{d.date_from} ← {d.doc_date}",
                         "صافي الربح المقفل": profit, "الحالة": status_label(d.status),
                         "سجّله": names.get(d.created_by_id, "")})
    return pd.DataFrame(rows, columns=["id", "رقم المستند", "السنة المالية", "صافي الربح المقفل", "الحالة", "سجّله"])


def _close_lines(session, doc: YearClose) -> list[PostingLine]:
    out, total = [], 0.0
    for a, v in _pl_balances(session, doc.doc_date):
        out.append(PostingLine(a.id, debit=-v if v < 0 else 0, credit=v if v > 0 else 0, memo=doc.memo))
        total += v
    total = r2(total)  # debit balance overall = loss
    re_acc = _key_account(session, "retained_earnings").id
    if total:
        out.append(PostingLine(re_acc, debit=total if total > 0 else 0, credit=-total if total < 0 else 0,
                               memo=doc.memo))
    return out


def _close_post(session, doc: YearClose, entry_id) -> None:
    later = session.scalar(select(YearClose.id).where(YearClose.year > doc.year, YearClose.status == APPROVED).limit(1))
    if later:
        raise BusinessError("فيه إقفال سنة بعدها معتمد")


def _close_void(session, doc: YearClose) -> None:
    later = session.scalar(select(YearClose.id).where(YearClose.year > doc.year, YearClose.status == APPROVED).limit(1))
    if later:
        raise BusinessError("فيه إقفال سنة بعدها معتمد؛ الغي اعتماده الأول")


def _close_describe(session, doc: YearClose):
    return "إقفال السنة المالية", f"{doc.date_from} إلى {doc.doc_date}", None


approval.register(approval.DocType(CLOSE_SOURCE, YearClose, CLOSE_SCREEN, _close_describe, _close_lines,
                                   on_post=_close_post, on_void=_close_void))
