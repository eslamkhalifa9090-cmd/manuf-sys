"""Compound runs (stage 1), production orders (stage 2) and the month-end overhead close.

Both stages pass through work in process (1303) in one approved document:
    Dr 1303 / Cr materials (WAC)          materials issued
    Dr 1303 / Cr 5301                     conversion applied at predetermined rates
    Dr 1303 / Cr 1206                     mold amortization for the shots run (stage 2)
    Dr output inventory / Cr 1303         good output at cost
    Dr 5402 / Cr 1303                     abnormal waste (stage 2)
The month-end close sets applied conversion (5301) against actual (5201-5208); the
difference (under / over-applied) goes to cost variances (5404).
"""
from datetime import date

import pandas as pd
from sqlalchemy import func, select

from core.db import session_scope
from core.models import Account, Item, ItemGroup, JournalEntry, JournalLine, Party, User
from core.models.costing import CONVERSION, ENERGY, MAINTENANCE, OTHER, WAGES
from core.models.documents import APPROVED
from core.models.ledger import ENTRY_POSTED
from core.models.molds import ACTIVE, AMORTIZED, PIECES_PER_COUNTER, Mold, MoldAmortization, MoldUsage
from core.models.production import CompoundRun, CompoundRunLine, OverheadClose, ProductionLine, ProductionOrder
from core.models.sales import SO_OPEN, SalesOrder
from core.services import approval, audit, boms, cost_rates, stock
from core.services import molds as mold_reg
from core.services.approval import PostingLine, r2
from core.services.doc_rules import check_access, check_editable, own_only, resubmit, status_label
from core.services.errors import BusinessError
from core.services.periods import ensure_open

COMPOUND_SCREEN = "compound_runs"
PRODUCTION_SCREEN = "production_orders"
CLOSE_SCREEN = "overhead_close"
RATE_FIELDS = (WAGES, ENERGY, MAINTENANCE, OTHER)
ACTUAL_PREFIX = "520"  # 5201-5208; 5209 (mold amortization by hand) is not applied through 5301
EPS = 1e-6


def _account(session, key: str) -> int:
    return mold_reg.system_account(session, key)


def _f(x) -> float:
    try:
        v = float(x or 0)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v != v else v


def _names(session):
    return dict(session.execute(select(User.id, User.full_name)).all())


def _group_kind(session, item_id: int) -> str | None:
    it = session.get(Item, item_id)
    return session.get(ItemGroup, it.group_id).kind if it else None


def _clean_lines(session, lines: list[dict], exclude_item: int | None = None) -> list[tuple[int, float]]:
    out = []
    for i, l in enumerate(lines, start=1):
        item_id, qty = l.get("item_id"), _f(l.get("qty"))
        if not item_id and not qty:
            continue
        if not item_id or session.get(Item, item_id) is None:
            raise BusinessError(f"السطر {i}: اختار الخامة")
        if item_id == exclude_item:
            raise BusinessError(f"السطر {i}: مينفعش الصنف يتصرف على نفسه")
        if qty <= 0:
            raise BusinessError(f"السطر {i}: الكمية لازم تكون أكبر من صفر")
        out.append((item_id, qty))
    if not out:
        raise BusinessError("لازم يتصرف خامة واحدة على الأقل")
    return out


def _closed_month(session, d: date) -> OverheadClose | None:
    return session.scalar(select(OverheadClose).where(OverheadClose.year == d.year, OverheadClose.month == d.month,
                                                      OverheadClose.status == APPROVED))


def _check_month_open(session, d: date) -> None:
    close = _closed_month(session, d)
    if close is not None:
        raise BusinessError(f"تحميل شهر {d.month}/{d.year} اتقفل ({close.doc_no}); الغي اعتماد التقفيل الأول")


def recipe_inputs(item_id: int, pieces: float) -> list[dict]:
    """The item's recipe scaled to a number of pieces (kilos for a compound)."""
    with session_scope() as s:
        return [{"item_id": c.id, "qty": round(q * pieces, 4)} for c, q in boms.per_piece_lines(s, item_id)]


# ---------------------------------------------------------------- stage 1: compound runs

def save_run(actor, run_id: int | None, doc_date: date, output_item_id: int, output_qty: float, lines: list[dict],
             conversion_rate: float = 0, memo: str = "") -> int:
    if doc_date is None:
        raise BusinessError("التاريخ مطلوب")
    if _f(output_qty) <= 0:
        raise BusinessError("كمية التضريبة الناتجة لازم تكون أكبر من صفر")
    if _f(conversion_rate) < 0:
        raise BusinessError("معدل التحميل مينفعش يكون بالسالب")
    with session_scope() as s:
        if _group_kind(s, output_item_id) != "compound":
            raise BusinessError("الناتج لازم يكون صنف من مجموعة التضريبة والخامات الوسيطة")
        clean = _clean_lines(s, lines, exclude_item=output_item_id)
        if run_id is None:
            check_access(actor, COMPOUND_SCREEN, "add")
            doc = CompoundRun(created_by_id=actor.id)
        else:
            doc = s.get(CompoundRun, run_id)
            if doc is None:
                raise BusinessError("المستند غير موجود")
            check_access(actor, COMPOUND_SCREEN, "edit", doc)
            check_editable(doc)
            resubmit(doc)
        ensure_open(s, doc_date)
        doc.doc_date, doc.output_item_id, doc.output_qty = doc_date, output_item_id, _f(output_qty)
        doc.conversion_rate, doc.memo = _f(conversion_rate), (memo or "").strip() or None
        doc.lines = [CompoundRunLine(item_id=i, qty=q) for i, q in clean]
        s.add(doc)
        s.flush()
        audit.log(s, actor, "create" if run_id is None else "update", "compound_run", doc.id, {"doc_no": doc.doc_no})
        return doc.id


def delete_run(actor, run_id: int) -> None:
    with session_scope() as s:
        doc = s.get(CompoundRun, run_id)
        if doc is None:
            return
        check_access(actor, COMPOUND_SCREEN, "delete", doc)
        check_editable(doc)
        audit.log(s, actor, "delete", "compound_run", doc.id, {"doc_no": doc.doc_no})
        s.delete(doc)


def get_run(run_id: int) -> dict:
    with session_scope() as s:
        d = s.get(CompoundRun, run_id)
        if d is None:
            raise BusinessError("المستند غير موجود")
        return {"id": d.id, "doc_no": d.doc_no, "doc_date": d.doc_date, "output_item_id": d.output_item_id,
                "output_qty": float(d.output_qty), "conversion_rate": float(d.conversion_rate or 0),
                "memo": d.memo or "", "status": d.status, "rejection_note": d.rejection_note,
                "lines": [{"item_id": l.item_id, "qty": float(l.qty)} for l in d.lines]}


def _run_plan(session, doc: CompoundRun):
    issues = stock.plan_outbound(session, [(l.item_id, float(l.qty), None) for l in doc.lines])
    material = r2(sum(m.value for m in issues))
    conversion = r2(float(doc.conversion_rate or 0) * float(doc.output_qty))
    total = r2(material + conversion)
    out = stock.PlannedMove(doc.output_item_id, qty_in=float(doc.output_qty),
                            unit_cost=round(total / float(doc.output_qty), 6))
    return issues, material, conversion, total, out


def _run_lines(session, doc: CompoundRun) -> list[PostingLine]:
    issues, material, conversion, total, out = _run_plan(session, doc)
    wip, memo = _account(session, "wip"), doc.memo or f"تشغيل تضريبة {doc.doc_no}"
    by_acc: dict[int, float] = {}
    for m in issues:
        a = stock.group_account(session, m.item_id)
        by_acc[a] = r2(by_acc.get(a, 0) + m.value)
    lines = [PostingLine(wip, debit=material, memo=memo)]
    lines += [PostingLine(a, credit=v, memo=memo) for a, v in by_acc.items()]
    if conversion:
        lines += [PostingLine(wip, debit=conversion, memo=f"تحميل {memo}"),
                  PostingLine(_account(session, "applied_conversion"), credit=conversion, memo=f"تحميل {memo}")]
    lines += [PostingLine(stock.group_account(session, doc.output_item_id), debit=total, memo=memo),
              PostingLine(wip, credit=total, memo=memo)]
    return lines


def _run_post(session, doc: CompoundRun, entry_id) -> None:
    _check_month_open(session, doc.doc_date)
    issues, *_, out = _run_plan(session, doc)
    stock.write_moves(session, doc, "compound_run", "production_issue", issues)
    stock.write_moves(session, doc, "compound_run", "production_output", [out])


def _run_void(session, doc: CompoundRun) -> None:
    _check_month_open(session, doc.doc_date)
    stock.void_moves(session, "compound_run", doc.id)


def _run_describe(session, doc: CompoundRun):
    it = session.get(Item, doc.output_item_id)
    return "تشغيل تضريبة", f"{it.name} - {float(doc.output_qty):g} {it.unit}", None


approval.register(approval.DocType("compound_run", CompoundRun, COMPOUND_SCREEN, _run_describe, _run_lines,
                                   on_post=_run_post, on_void=_run_void))


def runs_frame(actor) -> pd.DataFrame:
    with session_scope() as s:
        names, items = _names(s), {i.id: i for i in s.scalars(select(Item))}
        q = select(CompoundRun).order_by(CompoundRun.doc_date.desc(), CompoundRun.id.desc())
        if own_only(actor, COMPOUND_SCREEN):
            q = q.where(CompoundRun.created_by_id == actor.id)
        rows = []
        for d in s.scalars(q):
            out = items[d.output_item_id]
            kilos = sum(float(l.qty) for l in d.lines if boms.grams_per_unit(items[l.item_id].unit))
            loss = kilos - float(d.output_qty) if boms.grams_per_unit(out.unit) else None
            rows.append({"id": d.id, "رقم المستند": d.doc_no, "التاريخ": d.doc_date, "الناتج": out.name,
                         "الكمية": float(d.output_qty), "الوحدة": out.unit, "وزن الخامات": round(kilos, 3),
                         "فرق الوزن": round(loss, 3) if loss is not None else None,
                         "تحميل للوحدة": float(d.conversion_rate or 0), "الحالة": status_label(d.status),
                         "ملاحظة المراجع": d.rejection_note or "", "سجّله": names.get(d.created_by_id, "")})
    return pd.DataFrame(rows, columns=["id", "رقم المستند", "التاريخ", "الناتج", "الكمية", "الوحدة", "وزن الخامات",
                                       "فرق الوزن", "تحميل للوحدة", "الحالة", "ملاحظة المراجع", "سجّله"])


# ---------------------------------------------------------------- stage 2: production orders

def order_defaults(item_id: int, pieces: float, on_date: date | None = None) -> dict:
    """Recipe scaled to the pieces run, conversion rates in force, and the recipe's normal waste."""
    rates = cost_rates.rates_on(on_date)
    with session_scope() as s:
        bom = boms._bom(s, item_id)
        waste = float(bom.waste_pct) if bom else 0.0
    return {"lines": recipe_inputs(item_id, pieces), "normal_waste_pct": waste,
            "rates": {c: float(rates[c]["value"]) if c in rates else 0.0 for c in CONVERSION}}


def save_order(actor, order_id: int | None, doc_date: date, item_id: int, good_pieces: float, lines: list[dict],
               waste_pieces: float = 0, normal_waste_pct: float = 0, rates: dict | None = None,
               sales_order_id: int | None = None, mold_id: int | None = None, shots: float = 0,
               cavities: int | None = None, memo: str = "") -> int:
    if doc_date is None:
        raise BusinessError("التاريخ مطلوب")
    if _f(good_pieces) <= 0:
        raise BusinessError("عدد القطع السليمة لازم يكون أكبر من صفر")
    if _f(waste_pieces) < 0 or _f(normal_waste_pct) < 0:
        raise BusinessError("مينفعش قيم بالسالب")
    rates = rates or {}
    if any(_f(rates.get(c)) < 0 for c in RATE_FIELDS):
        raise BusinessError("معدلات التحميل مينفعش تبقى بالسالب")
    with session_scope() as s:
        if _group_kind(s, item_id) != "finished":
            raise BusinessError("المنتج لازم يكون صنف إنتاج تام")
        clean = _clean_lines(s, lines, exclude_item=item_id)
        if sales_order_id:
            so = s.get(SalesOrder, sales_order_id)
            if so is None or so.status != SO_OPEN:
                raise BusinessError("أمر العميل مش مفتوح")
            if all(l.item_id != item_id for l in so.lines):
                raise BusinessError("الصنف ده مش في أمر العميل")
        cav = 0
        if mold_id:
            m = s.get(Mold, mold_id)
            if m is None or m.status != ACTIVE:
                raise BusinessError("الأسطمبة غير موجودة أو خارج الخدمة")
            if _f(shots) <= 0:
                raise BusinessError("اكتب عدد الكبسات على الأسطمبة")
            cav = int(cavities or m.cavities)
            if not 1 <= cav <= m.cavities:
                raise BusinessError(f"التجاويف الشغالة لازم تكون بين 1 و {m.cavities}")
            if m.opening_date and doc_date <= m.opening_date:
                raise BusinessError("التاريخ قبل تاريخ الرصيد الافتتاحي للأسطمبة")
        if order_id is None:
            check_access(actor, PRODUCTION_SCREEN, "add")
            doc = ProductionOrder(created_by_id=actor.id)
        else:
            doc = s.get(ProductionOrder, order_id)
            if doc is None:
                raise BusinessError("المستند غير موجود")
            check_access(actor, PRODUCTION_SCREEN, "edit", doc)
            check_editable(doc)
            resubmit(doc)
        ensure_open(s, doc_date)
        doc.doc_date, doc.item_id, doc.sales_order_id = doc_date, item_id, sales_order_id or None
        doc.mold_id, doc.shots, doc.cavities = mold_id or None, _f(shots) if mold_id else 0, cav or 1
        doc.good_pieces, doc.waste_pieces, doc.normal_waste_pct = _f(good_pieces), _f(waste_pieces), _f(normal_waste_pct)
        for c in RATE_FIELDS:
            setattr(doc, c, _f(rates.get(c)))
        doc.memo = (memo or "").strip() or None
        doc.lines = [ProductionLine(item_id=i, qty=q) for i, q in clean]
        s.add(doc)
        s.flush()
        audit.log(s, actor, "create" if order_id is None else "update", "production_order", doc.id,
                  {"doc_no": doc.doc_no, "good": _f(good_pieces)})
        return doc.id


def delete_order(actor, order_id: int) -> None:
    with session_scope() as s:
        doc = s.get(ProductionOrder, order_id)
        if doc is None:
            return
        check_access(actor, PRODUCTION_SCREEN, "delete", doc)
        check_editable(doc)
        audit.log(s, actor, "delete", "production_order", doc.id, {"doc_no": doc.doc_no})
        s.delete(doc)


def get_order(order_id: int) -> dict:
    with session_scope() as s:
        d = s.get(ProductionOrder, order_id)
        if d is None:
            raise BusinessError("المستند غير موجود")
        return {"id": d.id, "doc_no": d.doc_no, "doc_date": d.doc_date, "item_id": d.item_id,
                "sales_order_id": d.sales_order_id, "mold_id": d.mold_id, "shots": float(d.shots or 0),
                "cavities": d.cavities, "good_pieces": float(d.good_pieces), "waste_pieces": float(d.waste_pieces or 0),
                "normal_waste_pct": float(d.normal_waste_pct or 0), "rates": {c: float(getattr(d, c) or 0)
                                                                            for c in RATE_FIELDS},
                "memo": d.memo or "", "status": d.status, "rejection_note": d.rejection_note,
                "lines": [{"item_id": l.item_id, "qty": float(l.qty)} for l in d.lines]}


def _unamortized_manual(session, mold_id: int) -> float:
    """Hand-typed usage after the last approved monthly amortization."""
    last = session.execute(select(MoldAmortization.year, MoldAmortization.month)
                           .where(MoldAmortization.status == APPROVED)
                           .order_by(MoldAmortization.year.desc(), MoldAmortization.month.desc()).limit(1)).first()
    after = mold_reg.month_end(*last) if last else None
    q = select(func.sum(MoldUsage.units)).where(MoldUsage.mold_id == mold_id, MoldUsage.production_order_id.is_(None))
    if after:
        q = q.where(MoldUsage.usage_date > after)
    return round(float(session.scalar(q) or 0), 4)


def _mold_charge(session, doc: ProductionOrder) -> tuple[float, float, float]:
    """(counter units, rate, amount) for the shots this order ran; nothing for a customer's mold."""
    if not doc.mold_id:
        return 0.0, 0.0, 0.0
    m = session.get(Mold, doc.mold_id)
    units = round(float(doc.shots) * int(doc.cavities) / PIECES_PER_COUNTER[m.counter_unit], 4)
    if m.ownership not in AMORTIZED:
        return units, 0.0, 0.0
    base = max(r2(mold_reg.cost_total(session, m, doc.doc_date) - float(m.salvage_value or 0)
                  - mold_reg.accumulated(session, m)), 0.0)
    # Hand-typed usage the monthly document has not amortized yet stays in the remaining life,
    # so the rate matches the accumulated amount it is measured against.
    remaining = (mold_reg.life_total(session, m, doc.doc_date) - float(m.opening_usage or 0)
                 - mold_reg.usage_units(session, m.id) + _unamortized_manual(session, m.id))
    if remaining <= EPS:
        return units, 0.0, 0.0
    rate = round(base / remaining, 6)
    amount = base if units >= remaining else min(r2(units * rate), base)
    return units, rate, r2(amount)


def costing(session, doc: ProductionOrder) -> dict:
    """The order's cost build-up as it would post now."""
    issues = stock.plan_outbound(session, [(l.item_id, float(l.qty), None) for l in doc.lines])
    material = r2(sum(m.value for m in issues))
    run = doc.pieces_run
    conversion = r2(sum(float(getattr(doc, c) or 0) for c in RATE_FIELDS) * run)
    units, rate, mold_amount = _mold_charge(session, doc)
    total = r2(material + conversion + mold_amount)
    per_piece = total / run if run else 0.0
    normal = float(doc.good_pieces) * float(doc.normal_waste_pct or 0) / 100
    abnormal_pieces = max(float(doc.waste_pieces or 0) - normal, 0.0)
    abnormal = r2(abnormal_pieces * per_piece)
    finished = r2(total - abnormal)
    item = session.get(Item, doc.item_id)
    ppu = float(item.pieces_per_unit or 1)
    qty = round(float(doc.good_pieces) / ppu, 4)
    out = stock.PlannedMove(doc.item_id, qty_in=qty, unit_cost=round(finished / qty, 6) if qty else 0.0)
    return {"issues": issues, "material": material, "conversion": conversion, "mold_units": units, "mold_rate": rate,
            "mold_amount": mold_amount, "total": total, "abnormal_pieces": round(abnormal_pieces, 4),
            "abnormal": abnormal, "finished": finished, "output": out, "qty": qty,
            "cost_per_piece": round(finished / float(doc.good_pieces), 6) if doc.good_pieces else 0.0,
            "cost_per_unit": round(finished / qty, 6) if qty else 0.0}


def preview_costing(order_id: int) -> dict:
    with session_scope() as s:
        doc = s.get(ProductionOrder, order_id)
        c = costing(s, doc)
        c.pop("issues"), c.pop("output")
        return c


def _order_lines(session, doc: ProductionOrder) -> list[PostingLine]:
    c = costing(session, doc)
    wip, memo = _account(session, "wip"), doc.memo or f"أمر إنتاج {doc.doc_no}"
    by_acc: dict[int, float] = {}
    for m in c["issues"]:
        a = stock.group_account(session, m.item_id)
        by_acc[a] = r2(by_acc.get(a, 0) + m.value)
    lines = [PostingLine(wip, debit=c["material"], memo=f"خامات {memo}")]
    lines += [PostingLine(a, credit=v, memo=f"خامات {memo}") for a, v in by_acc.items()]
    if c["conversion"]:
        lines += [PostingLine(wip, debit=c["conversion"], memo=f"تحميل {memo}"),
                  PostingLine(_account(session, "applied_conversion"), credit=c["conversion"], memo=f"تحميل {memo}")]
    if c["mold_amount"]:
        mm = f"إهلاك أسطمبة {memo} - {c['mold_units']:g} وحدة"
        lines += [PostingLine(wip, debit=c["mold_amount"], memo=mm),
                  PostingLine(_account(session, "mold_acc_amortization"), credit=c["mold_amount"], memo=mm)]
    lines += [PostingLine(stock.group_account(session, doc.item_id), debit=c["finished"], memo=f"استلام {memo}"),
              PostingLine(wip, credit=c["finished"], memo=f"استلام {memo}")]
    if c["abnormal"]:
        lines += [PostingLine(_account(session, "abnormal_waste"), debit=c["abnormal"],
                              memo=f"هالك غير طبيعي {memo} - {c['abnormal_pieces']:g} قطعة"),
                  PostingLine(wip, credit=c["abnormal"], memo=f"هالك غير طبيعي {memo}")]
    return lines


def _order_post(session, doc: ProductionOrder, entry_id) -> None:
    _check_month_open(session, doc.doc_date)
    if doc.mold_id:
        m = session.get(Mold, doc.mold_id)
        if m.status != ACTIVE:
            raise BusinessError(f"الأسطمبة {m.code} خارج الخدمة")
        mold_reg._check_usage_open(session, doc.doc_date)
    c = costing(session, doc)
    doc.mold_units, doc.mold_rate, doc.mold_amount = c["mold_units"], c["mold_rate"], c["mold_amount"]
    stock.write_moves(session, doc, "production_order", "production_issue", c["issues"])
    stock.write_moves(session, doc, "production_order", "production_output", [c["output"]])
    if doc.mold_id:
        m = session.get(Mold, doc.mold_id)
        session.add(MoldUsage(mold_id=m.id, usage_date=doc.doc_date, shots=float(doc.shots), cavities=doc.cavities,
                              pieces=float(doc.shots) * doc.cavities, units=c["mold_units"], item_id=doc.item_id,
                              production_order_id=doc.id, memo=doc.doc_no, created_by_id=doc.created_by_id))
        session.flush()


def _order_void(session, doc: ProductionOrder) -> None:
    _check_month_open(session, doc.doc_date)
    if doc.mold_id:
        later = session.scalar(select(ProductionOrder.id).where(
            ProductionOrder.mold_id == doc.mold_id, ProductionOrder.status == APPROVED, ProductionOrder.id != doc.id,
            (ProductionOrder.doc_date > doc.doc_date)
            | ((ProductionOrder.doc_date == doc.doc_date) & (ProductionOrder.id > doc.id))).limit(1))
        if later:
            raise BusinessError("فيه أمر إنتاج معتمد بعده على نفس الأسطمبة؛ الغي اعتماده الأول عشان معدل الإهلاك")
        mold_reg._check_usage_open(session, doc.doc_date)
        for u in session.scalars(select(MoldUsage).where(MoldUsage.production_order_id == doc.id)):
            session.delete(u)
    stock.void_moves(session, "production_order", doc.id)
    doc.mold_units = doc.mold_rate = doc.mold_amount = 0


def _order_describe(session, doc: ProductionOrder):
    it = session.get(Item, doc.item_id)
    return "أمر إنتاج", f"{it.name} - {float(doc.good_pieces):g} قطعة سليمة", None


approval.register(approval.DocType("production_order", ProductionOrder, PRODUCTION_SCREEN, _order_describe,
                                   _order_lines, on_post=_order_post, on_void=_order_void))


def orders_frame(actor, date_from: date | None = None, date_to: date | None = None,
                 approved_only: bool = False) -> pd.DataFrame:
    """One row per production order with its yield and (approved) cost build-up."""
    with session_scope() as s:
        names, items = _names(s), {i.id: i for i in s.scalars(select(Item))}
        molds = {m.id: m for m in s.scalars(select(Mold))}
        q = select(ProductionOrder).order_by(ProductionOrder.doc_date.desc(), ProductionOrder.id.desc())
        if date_from:
            q = q.where(ProductionOrder.doc_date >= date_from)
        if date_to:
            q = q.where(ProductionOrder.doc_date <= date_to)
        if approved_only:
            q = q.where(ProductionOrder.status == APPROVED)
        if own_only(actor, PRODUCTION_SCREEN):
            q = q.where(ProductionOrder.created_by_id == actor.id)
        rows = []
        for d in s.scalars(q):
            it = items[d.item_id]
            run = d.pieces_run
            value = None
            if d.status == APPROVED and d.journal_entry_id:
                fg_acc = stock.group_account(s, d.item_id)
                value = r2(s.scalar(select(func.sum(JournalLine.debit)).where(
                    JournalLine.entry_id == d.journal_entry_id, JournalLine.account_id == fg_acc)) or 0)
            rows.append({"id": d.id, "رقم الأمر": d.doc_no, "التاريخ": d.doc_date, "المنتج": it.name,
                         "أمر العميل": f"SO-{d.sales_order_id:06d}" if d.sales_order_id else "",
                         "الأسطمبة": molds[d.mold_id].code if d.mold_id else "", "الكبسات": float(d.shots or 0),
                         "قطع سليمة": float(d.good_pieces), "هالك": float(d.waste_pieces or 0),
                         "% الهالك": round(float(d.waste_pieces or 0) / run * 100, 2) if run else 0.0,
                         "إهلاك الأسطمبة": float(d.mold_amount or 0), "تكلفة الإنتاج التام": value,
                         "تكلفة القطعة": round(value / float(d.good_pieces), 4) if value else None,
                         "الحالة": status_label(d.status), "ملاحظة المراجع": d.rejection_note or "",
                         "سجّله": names.get(d.created_by_id, "")})
    return pd.DataFrame(rows, columns=["id", "رقم الأمر", "التاريخ", "المنتج", "أمر العميل", "الأسطمبة", "الكبسات",
                                       "قطع سليمة", "هالك", "% الهالك", "إهلاك الأسطمبة", "تكلفة الإنتاج التام",
                                       "تكلفة القطعة", "الحالة", "ملاحظة المراجع", "سجّله"])


# ---------------------------------------------------------------- month-end overhead close

def _month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), mold_reg.month_end(year, month)


def overhead_figures(session, year: int, month: int) -> dict:
    """Actual conversion per account (5201-5208) and applied (5301) posted in the month."""
    start, end = _month_bounds(year, month)
    q = (select(Account.id, Account.code, Account.name_ar,
                func.sum(JournalLine.debit - JournalLine.credit))
         .join(JournalLine, JournalLine.account_id == Account.id)
         .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
         .where(JournalEntry.status == ENTRY_POSTED, JournalEntry.entry_date >= start, JournalEntry.entry_date <= end,
                JournalEntry.source_type != "overhead_close")
         .group_by(Account.id, Account.code, Account.name_ar))
    actual, applied = [], 0.0
    applied_acc = _account(session, "applied_conversion")
    for acc_id, code, name, bal in session.execute(q):
        if acc_id == applied_acc:
            applied = r2(-(bal or 0))
        elif code.startswith(ACTUAL_PREFIX):
            if r2(bal):
                actual.append({"account_id": acc_id, "code": code, "name": name, "amount": r2(bal)})
    total_actual = r2(sum(a["amount"] for a in actual))
    return {"actual": sorted(actual, key=lambda a: a["code"]), "total_actual": total_actual, "applied": applied,
            "variance": r2(total_actual - applied)}


def close_preview(year: int, month: int) -> dict:
    with session_scope() as s:
        return overhead_figures(s, int(year), int(month))


def compute_close(actor, year: int, month: int, note: str = "") -> int:
    if not 1 <= int(month) <= 12:
        raise BusinessError("الشهر غير صحيح")
    year, month = int(year), int(month)
    with session_scope() as s:
        ensure_open(s, mold_reg.month_end(year, month))
        f = overhead_figures(s, year, month)
        if not f["applied"] and not f["total_actual"]:
            raise BusinessError("مفيش تكاليف فعلية ولا محملة في الشهر ده")
        doc = s.scalar(select(OverheadClose).where(OverheadClose.year == year, OverheadClose.month == month))
        if doc is None:
            check_access(actor, CLOSE_SCREEN, "add")
            doc = OverheadClose(created_by_id=actor.id, year=year, month=month)
            s.add(doc)
        else:
            check_access(actor, CLOSE_SCREEN, "edit", doc)
            check_editable(doc)
            resubmit(doc)
        doc.doc_date = mold_reg.month_end(year, month)
        doc.applied, doc.actual = f["applied"], f["total_actual"]
        doc.note = (note or "").strip() or None
        doc.memo = f"تقفيل التكاليف التحويلية عن شهر {month}/{year}"
        s.flush()
        audit.log(s, actor, "compute", "overhead_close", doc.id, {"doc_no": doc.doc_no, "variance": f["variance"]})
        return doc.id


def delete_close(actor, doc_id: int) -> None:
    with session_scope() as s:
        doc = s.get(OverheadClose, doc_id)
        if doc is None:
            return
        check_access(actor, CLOSE_SCREEN, "delete", doc)
        check_editable(doc)
        audit.log(s, actor, "delete", "overhead_close", doc.id, {"doc_no": doc.doc_no})
        s.delete(doc)


def closes_frame(actor) -> pd.DataFrame:
    with session_scope() as s:
        names = _names(s)
        q = select(OverheadClose).order_by(OverheadClose.year.desc(), OverheadClose.month.desc())
        rows = [{"id": d.id, "رقم المستند": d.doc_no, "الشهر": f"{d.month:02d}/{d.year}", "المحمّل": float(d.applied),
                 "الفعلي": float(d.actual), "الفرق": r2(float(d.actual) - float(d.applied)),
                 "النتيجة": "تحميل بأقل من الفعلي" if float(d.actual) > float(d.applied) else
                 ("تحميل بأكتر من الفعلي" if float(d.actual) < float(d.applied) else "متطابق"),
                 "الحالة": status_label(d.status), "سجّله": names.get(d.created_by_id, "")} for d in s.scalars(q)]
    return pd.DataFrame(rows, columns=["id", "رقم المستند", "الشهر", "المحمّل", "الفعلي", "الفرق", "النتيجة", "الحالة",
                                       "سجّله"])


def _close_lines(session, doc: OverheadClose) -> list[PostingLine]:
    f = overhead_figures(session, doc.year, doc.month)
    memo = doc.memo
    lines = []
    if f["applied"]:
        lines.append(PostingLine(_account(session, "applied_conversion"), debit=f["applied"], memo=memo))
    for a in f["actual"]:
        amt = a["amount"]
        lines.append(PostingLine(a["account_id"], credit=amt if amt > 0 else 0, debit=-amt if amt < 0 else 0, memo=memo))
    v = f["variance"]
    if v:
        lines.append(PostingLine(_account(session, "cost_variance"), debit=v if v > 0 else 0, credit=-v if v < 0 else 0,
                                 memo=f"فرق التحميل {doc.month}/{doc.year}"))
    return lines


def _close_describe(session, doc: OverheadClose):
    return "تقفيل التكاليف التحويلية", f"شهر {doc.month}/{doc.year}", r2(float(doc.actual) - float(doc.applied))


approval.register(approval.DocType("overhead_close", OverheadClose, CLOSE_SCREEN, _close_describe, _close_lines))


# ---------------------------------------------------------------- order profitability

def order_profit_frame(date_from: date | None = None, date_to: date | None = None) -> pd.DataFrame:
    """Per customer order: what was delivered and earned, against the quote it came from."""
    from core.models.sales import SALE_RETURN, SalesInvoice
    from core.services import quotes
    from core.services.sales import _net_amounts

    with session_scope() as s:
        names = {p.id: p.name for p in s.scalars(select(Party))}
        q = select(SalesOrder).order_by(SalesOrder.order_date.desc(), SalesOrder.id.desc())
        if date_from:
            q = q.where(SalesOrder.order_date >= date_from)
        if date_to:
            q = q.where(SalesOrder.order_date <= date_to)
        orders = list(s.scalars(q))
        rows = []
        for o in orders:
            revenue = cost = units = 0.0
            for inv in s.scalars(select(SalesInvoice).where(SalesInvoice.sales_order_id == o.id,
                                                            SalesInvoice.status == APPROVED)):
                sign = -1 if inv.kind == SALE_RETURN else 1
                for line, net in zip(inv.lines, _net_amounts(inv)):
                    if line.item_id:
                        revenue += sign * net
                        cost += sign * float(line.qty) * float(line.unit_cost or 0)
                        units += sign * float(line.qty)
            made = [d for d in s.scalars(select(ProductionOrder).where(ProductionOrder.sales_order_id == o.id,
                                                                       ProductionOrder.status == APPROVED))]
            prod_value = prod_units = 0.0
            for d in made:
                fg_acc = stock.group_account(s, d.item_id)
                prod_value += float(s.scalar(select(func.sum(JournalLine.debit)).where(
                    JournalLine.entry_id == d.journal_entry_id, JournalLine.account_id == fg_acc)) or 0)
                prod_units += float(d.good_pieces) / float(s.get(Item, d.item_id).pieces_per_unit or 1)
            quote_cost = quote_price = None
            if o.quote_id:
                qd = quotes.get_quote(o.quote_id)
                r = quotes.calc(qd)[qd["method"]]
                ppu = float(qd["pieces_per_unit"] or 1)
                quote_cost, quote_price = r["production"] * ppu, (qd.get("offered_price") or r["price_unit"])
            rows.append({"رقم الأمر": o.doc_no, "العميل": names.get(o.party_id, ""), "التاريخ": o.order_date,
                         "الكمية المسلّمة": round(units, 4), "صافي المبيعات": r2(revenue), "تكلفة المبيعات": r2(cost),
                         "مجمل الربح": r2(revenue - cost),
                         "% مجمل الربح": round((revenue - cost) / revenue * 100, 2) if revenue else None,
                         "سعر الوحدة الفعلي": round(revenue / units, 4) if units else None,
                         "سعر الوحدة في العرض": round(float(quote_price), 4) if quote_price else None,
                         "تكلفة إنتاج الوحدة الفعلية": round(prod_value / prod_units, 4) if prod_units else None,
                         "تكلفة إنتاج الوحدة في العرض": round(quote_cost, 4) if quote_cost else None})
    return pd.DataFrame(rows, columns=["رقم الأمر", "العميل", "التاريخ", "الكمية المسلّمة", "صافي المبيعات",
                                       "تكلفة المبيعات", "مجمل الربح", "% مجمل الربح", "سعر الوحدة الفعلي",
                                       "سعر الوحدة في العرض", "تكلفة إنتاج الوحدة الفعلية",
                                       "تكلفة إنتاج الوحدة في العرض"])
