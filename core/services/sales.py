"""Customer orders, sales invoices and returns.

The invoice is also the delivery note. Approval posts the sale and takes the goods out of
stock at weighted average cost (Dr 5101 / Cr the item's inventory account). A line may sell
a mold to the customer (Cr 4102); a mold from the register leaves the books with it
(Dr 5102 book value, Dr 1206, Cr 1106). A return brings goods back at the cost they left with.
"""
from datetime import date

import pandas as pd
from sqlalchemy import func, select

from core.db import session_scope
from core.models import Account, Item, Party, PriceQuote, User
from core.models.documents import APPROVED
from core.models.inventory import CASH, CREDIT, PAYMENT_TERMS
from core.models.molds import ACTIVE, AMORTIZED, RETIRED, Mold, MoldAmortization
from core.models.parties import BOTH, CUSTOMER
from core.models.production import ProductionOrder
from core.models.sales import (ORDER_STATUSES, SALE_INVOICE, SALE_KINDS, SALE_RETURN, SO_CANCELLED, SO_CLOSED, SO_OPEN,
                               SalesInvoice, SalesLine, SalesOrder, SalesOrderLine)
from core.services import approval, audit, stock
from core.services import molds as mold_reg
from core.services.approval import PostingLine, r2
from core.services.doc_rules import check_access, check_editable, own_only, resubmit, status_label
from core.services.errors import BusinessError
from core.services.periods import ensure_open

ORDER_SCREEN = "sales_orders"
SCREEN = "sales_invoices"
TREASURY_PREFIX = "15"
EPS = 1e-6


def _account(session, key: str) -> int:
    return mold_reg.system_account(session, key)


def _customer(session, party_id) -> Party:
    p = session.get(Party, party_id) if party_id else None
    if p is None or p.kind not in (CUSTOMER, BOTH):
        raise BusinessError("اختار العميل")
    return p


def _f(x) -> float:
    try:
        v = float(x or 0)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v != v else v


# ---------------------------------------------------------------- orders

def save_order(actor, order_id: int | None, order_date: date, party_id: int, lines: list[dict],
               delivery_date: date | None = None, customer_ref: str = "", quote_id: int | None = None,
               notes: str = "") -> int:
    """lines: dicts with item_id, qty (item unit), unit_price, mold_id, description; an `id` keeps an existing line."""
    if order_date is None:
        raise BusinessError("تاريخ الأمر مطلوب")
    with session_scope() as s:
        _customer(s, party_id)
        if quote_id and s.get(PriceQuote, quote_id) is None:
            raise BusinessError("عرض السعر غير موجود")
        if order_id is None:
            if not actor.can(ORDER_SCREEN, "add"):
                raise BusinessError("ليس لديك صلاحية الإضافة")
            order = SalesOrder(created_by_id=actor.id)
            existing = {}
        else:
            if not actor.can(ORDER_SCREEN, "edit"):
                raise BusinessError("ليس لديك صلاحية التعديل")
            order = s.get(SalesOrder, order_id)
            if order is None:
                raise BusinessError("الأمر غير موجود")
            if order.status != SO_OPEN:
                raise BusinessError("الأمر مقفول أو ملغي؛ افتحه الأول")
            existing = {l.id: l for l in order.lines}
        delivered = _delivered_by_line(s, order.id) if order_id else {}
        kept, clean = set(), []
        for i, l in enumerate(lines, start=1):
            item_id, qty = l.get("item_id"), _f(l.get("qty"))
            if not item_id and not qty:
                continue
            if not item_id or s.get(Item, item_id) is None:
                raise BusinessError(f"السطر {i}: اختار الصنف")
            if qty <= 0:
                raise BusinessError(f"السطر {i}: الكمية لازم تكون أكبر من صفر")
            if _f(l.get("unit_price")) < 0:
                raise BusinessError(f"السطر {i}: السعر مينفعش يكون سالب")
            if l.get("mold_id") and s.get(Mold, l["mold_id"]) is None:
                raise BusinessError(f"السطر {i}: الأسطمبة غير موجودة")
            line = existing.get(l.get("id")) if l.get("id") else None
            if line is not None:
                if line.item_id != item_id and delivered.get(line.id):
                    raise BusinessError(f"السطر {i}: اتسلّم منه؛ مينفعش تغيّر الصنف")
                if qty + EPS < delivered.get(line.id, 0):
                    raise BusinessError(f"السطر {i}: الكمية أقل من اللي اتسلّم ({delivered[line.id]:g})")
                kept.add(line.id)
            else:
                line = SalesOrderLine()
            line.item_id, line.qty, line.unit_price = item_id, qty, _f(l.get("unit_price"))
            line.mold_id = l.get("mold_id") or None
            line.description = (l.get("description") or "").strip() or None
            clean.append(line)
        if not clean:
            raise BusinessError("الأمر لازم يكون فيه صنف واحد على الأقل")
        for lid in set(existing) - kept:
            if delivered.get(lid):
                raise BusinessError("فيه سطر اتسلّم منه؛ مينفعش تمسحه")
        order.order_date, order.party_id, order.delivery_date = order_date, party_id, delivery_date
        order.customer_ref = (customer_ref or "").strip() or None
        order.quote_id, order.notes = quote_id or None, (notes or "").strip() or None
        order.lines = clean
        s.add(order)
        s.flush()
        audit.log(s, actor, "create" if order_id is None else "update", "sales_order", order.id,
                  {"doc_no": order.doc_no, "lines": len(clean)})
        return order.id


def set_order_status(actor, order_id: int, status: str) -> None:
    if status not in ORDER_STATUSES:
        raise BusinessError("حالة غير معروفة")
    if not actor.can(ORDER_SCREEN, "edit"):
        raise BusinessError("ليس لديك صلاحية التعديل")
    with session_scope() as s:
        order = s.get(SalesOrder, order_id)
        if order is None:
            raise BusinessError("الأمر غير موجود")
        if status == SO_CANCELLED and any(_delivered_by_line(s, order.id).values()):
            raise BusinessError("الأمر اتسلّم منه؛ اقفله بدل الإلغاء")
        order.status = status
        audit.log(s, actor, "status", "sales_order", order.id, {"doc_no": order.doc_no, "status": status})


def delete_order(actor, order_id: int) -> None:
    if not actor.can(ORDER_SCREEN, "delete"):
        raise BusinessError("ليس لديك صلاحية الحذف")
    with session_scope() as s:
        order = s.get(SalesOrder, order_id)
        if order is None:
            return
        used = [select(SalesInvoice.id).where(SalesInvoice.sales_order_id == order_id),
                select(ProductionOrder.id).where(ProductionOrder.sales_order_id == order_id)]
        if any(s.scalar(q.limit(1)) is not None for q in used):
            raise BusinessError("الأمر عليه فواتير أو أوامر إنتاج؛ اقفله أو الغيه بدل الحذف")
        audit.log(s, actor, "delete", "sales_order", order.id, {"doc_no": order.doc_no})
        s.delete(order)


def _delivered_by_line(session, order_id: int) -> dict[int, float]:
    """Net quantity delivered per order line: approved invoices less approved returns."""
    q = (select(SalesLine.order_line_id, SalesInvoice.kind, func.sum(SalesLine.qty))
         .join(SalesInvoice, SalesInvoice.id == SalesLine.invoice_id)
         .join(SalesOrderLine, SalesOrderLine.id == SalesLine.order_line_id)
         .where(SalesOrderLine.order_id == order_id, SalesInvoice.status == APPROVED)
         .group_by(SalesLine.order_line_id, SalesInvoice.kind))
    out: dict[int, float] = {}
    for lid, kind, qty in session.execute(q):
        out[lid] = out.get(lid, 0.0) + (-1 if kind == SALE_RETURN else 1) * float(qty or 0)
    return out


def _produced(session, order_id: int) -> dict[int, float]:
    """Good output of approved production orders for the order, per item, in the item's unit."""
    q = (select(ProductionOrder.item_id, func.sum(ProductionOrder.good_pieces))
         .where(ProductionOrder.sales_order_id == order_id, ProductionOrder.status == APPROVED)
         .group_by(ProductionOrder.item_id))
    out = {}
    for item_id, pieces in session.execute(q):
        ppu = float(session.get(Item, item_id).pieces_per_unit or 1)
        out[item_id] = round(float(pieces or 0) / ppu, 4)
    return out


def order_progress(order_id: int) -> pd.DataFrame:
    """Per line: ordered, produced, delivered and remaining."""
    with session_scope() as s:
        order = s.get(SalesOrder, order_id)
        if order is None:
            raise BusinessError("الأمر غير موجود")
        delivered, produced = _delivered_by_line(s, order_id), _produced(s, order_id)
        rows, left = [], dict(produced)
        for l in order.lines:
            item = s.get(Item, l.item_id)
            made = min(left.get(l.item_id, 0.0), float(l.qty))
            left[l.item_id] = left.get(l.item_id, 0.0) - made
            got = delivered.get(l.id, 0.0)
            rows.append({"line_id": l.id, "item_id": l.item_id, "الصنف": item.name, "الوحدة": item.unit,
                         "المطلوب": float(l.qty), "اتنتج": made, "اتسلّم": got,
                         "الباقي": round(max(float(l.qty) - got, 0.0), 4), "السعر": float(l.unit_price),
                         "القيمة": r2(float(l.qty) * float(l.unit_price))})
    return pd.DataFrame(rows, columns=["line_id", "item_id", "الصنف", "الوحدة", "المطلوب", "اتنتج", "اتسلّم", "الباقي",
                                       "السعر", "القيمة"])


def get_order(order_id: int) -> dict:
    with session_scope() as s:
        o = s.get(SalesOrder, order_id)
        if o is None:
            raise BusinessError("الأمر غير موجود")
        return {"id": o.id, "doc_no": o.doc_no, "order_date": o.order_date, "party_id": o.party_id,
                "delivery_date": o.delivery_date, "customer_ref": o.customer_ref or "", "quote_id": o.quote_id,
                "status": o.status, "notes": o.notes or "",
                "lines": [{"id": l.id, "item_id": l.item_id, "qty": float(l.qty), "unit_price": float(l.unit_price),
                           "mold_id": l.mold_id, "description": l.description or ""} for l in o.lines]}


def order_options(party_id: int | None = None, open_only: bool = True) -> dict[int, str]:
    with session_scope() as s:
        names = dict(s.execute(select(Party.id, Party.name)).all())
        q = select(SalesOrder).order_by(SalesOrder.id.desc())
        if open_only:
            q = q.where(SalesOrder.status == SO_OPEN)
        if party_id:
            q = q.where(SalesOrder.party_id == party_id)
        return {o.id: f"{o.doc_no} - {names.get(o.party_id, '')}" + (f" ({o.customer_ref})" if o.customer_ref else "")
                for o in s.scalars(q)}


def orders_frame(status: str | None = None, party_id: int | None = None) -> pd.DataFrame:
    with session_scope() as s:
        names = dict(s.execute(select(Party.id, Party.name)).all())
        q = select(SalesOrder).order_by(SalesOrder.order_date.desc(), SalesOrder.id.desc())
        if status:
            q = q.where(SalesOrder.status == status)
        if party_id:
            q = q.where(SalesOrder.party_id == party_id)
        rows = []
        for o in s.scalars(q):
            delivered = _delivered_by_line(s, o.id)
            ordered = sum(float(l.qty) for l in o.lines)
            got = sum(delivered.values())
            rows.append({"id": o.id, "رقم الأمر": o.doc_no, "التاريخ": o.order_date, "العميل": names.get(o.party_id, ""),
                         "رقم طلبية العميل": o.customer_ref or "", "تاريخ التسليم": o.delivery_date,
                         "القيمة": r2(sum(float(l.qty) * float(l.unit_price) for l in o.lines)),
                         "% التسليم": round(got / ordered * 100, 1) if ordered else 0.0,
                         "الحالة": ORDER_STATUSES[o.status]})
    return pd.DataFrame(rows, columns=["id", "رقم الأمر", "التاريخ", "العميل", "رقم طلبية العميل", "تاريخ التسليم",
                                       "القيمة", "% التسليم", "الحالة"])


def quote_to_order(quote_id: int) -> dict:
    """Party, item, quantity, price per unit and mold from a price quote."""
    from core.services import quotes

    q = quotes.get_quote(quote_id)
    price = q.get("offered_price") or quotes.calc(q)[q["method"]]["price_unit"]
    return {"party_id": q.get("party_id"), "quote_id": quote_id,
            "lines": [{"item_id": q["item_id"], "qty": q["qty"], "unit_price": round(float(price), 4),
                       "mold_id": q.get("mold_id"), "description": q["doc_no"]}]}


# ---------------------------------------------------------------- invoices

def save_invoice(actor, invoice_id: int | None, kind: str, doc_date: date, party_id: int, payment: str,
                 lines: list[dict], discount: float = 0, treasury_account_id: int | None = None,
                 sales_order_id: int | None = None, return_of_id: int | None = None, dispatch_no: str = "",
                 memo: str = "") -> int:
    """lines: dicts with item_id or mold_id, qty, unit_price, order_line_id, description."""
    if kind not in SALE_KINDS:
        raise BusinessError("نوع المستند غير معروف")
    if payment not in PAYMENT_TERMS:
        raise BusinessError("طريقة السداد غير معروفة")
    if doc_date is None:
        raise BusinessError("التاريخ مطلوب")
    discount = r2(discount or 0)
    if kind == SALE_RETURN and discount:
        raise BusinessError("المرتجع مفيهوش خصم؛ اكتب السعر بعد الخصم")
    with session_scope() as s:
        _customer(s, party_id)
        if payment == CASH:
            t = s.get(Account, treasury_account_id) if treasury_account_id else None
            if t is None or not t.code.startswith(TREASURY_PREFIX) or not t.is_postable:
                raise BusinessError("البيع النقدي محتاج تختار الخزينة أو المحفظة أو البنك")
        else:
            treasury_account_id = None
        order = s.get(SalesOrder, sales_order_id) if sales_order_id else None
        if sales_order_id and (order is None or order.party_id != party_id):
            raise BusinessError("أمر العميل مش بتاع العميل ده")
        if order is not None and kind == SALE_INVOICE and order.status != SO_OPEN:
            raise BusinessError("أمر العميل مقفول أو ملغي")
        order_lines = {l.id: l for l in order.lines} if order else {}
        original = None
        if kind == SALE_RETURN and return_of_id:
            original = s.get(SalesInvoice, return_of_id)
            if (original is None or original.kind != SALE_INVOICE or original.party_id != party_id
                    or original.status != APPROVED):
                raise BusinessError("الفاتورة الأصلية لازم تكون فاتورة معتمدة لنفس العميل")
        else:
            return_of_id = None
        clean = []
        for i, l in enumerate(lines, start=1):
            item_id, mold_id = l.get("item_id"), l.get("mold_id")
            qty, price = _f(l.get("qty")), _f(l.get("unit_price"))
            if not item_id and not mold_id and not qty and not price:
                continue
            if bool(item_id) == bool(mold_id):
                raise BusinessError(f"السطر {i}: اختار صنف أو أسطمبة (واحد بس)")
            if qty <= 0:
                raise BusinessError(f"السطر {i}: الكمية لازم تكون أكبر من صفر")
            if price < 0:
                raise BusinessError(f"السطر {i}: السعر مينفعش يكون سالب")
            if item_id and s.get(Item, item_id) is None:
                raise BusinessError(f"السطر {i}: الصنف غير موجود")
            if mold_id:
                m = s.get(Mold, mold_id)
                if kind == SALE_RETURN:
                    raise BusinessError(f"السطر {i}: مرتجع الأسطمبة بيتسجل يدوي بقيد")
                if m is None:
                    raise BusinessError(f"السطر {i}: الأسطمبة غير موجودة")
                if m.ownership not in AMORTIZED:
                    raise BusinessError(f"السطر {i}: الأسطمبة دي ملك العميل أصلاً")
                if m.status != ACTIVE:
                    raise BusinessError(f"السطر {i}: الأسطمبة خارج الخدمة أو اتباعت")
                if qty != 1:
                    raise BusinessError(f"السطر {i}: الأسطمبة بتتباع بكمية 1")
            olid = l.get("order_line_id") or None
            if olid:
                ol = order_lines.get(olid)
                if ol is None or ol.item_id != item_id:
                    raise BusinessError(f"السطر {i}: مش من أصناف أمر العميل")
            clean.append(SalesLine(item_id=item_id or None, mold_id=mold_id or None, order_line_id=olid, qty=qty,
                                   unit_price=price, description=(l.get("description") or "").strip() or None))
        if not clean:
            raise BusinessError("الفاتورة لازم يكون فيها سطر واحد على الأقل")
        if original is not None:
            sold: dict[int, float] = {}
            for l in original.lines:
                if l.item_id:
                    sold[l.item_id] = sold.get(l.item_id, 0.0) + float(l.qty)
            prior = s.execute(select(SalesLine.item_id, func.sum(SalesLine.qty))
                              .join(SalesInvoice, SalesInvoice.id == SalesLine.invoice_id)
                              .where(SalesInvoice.return_of_id == original.id, SalesInvoice.id != (invoice_id or 0))
                              .group_by(SalesLine.item_id)).all()
            for item_id, q in prior:
                sold[item_id] = sold.get(item_id, 0.0) - float(q or 0)
            back: dict[int, float] = {}
            for l in clean:
                back[l.item_id] = back.get(l.item_id, 0.0) + l.qty
            for item_id, q in back.items():
                if q > sold.get(item_id, 0.0) + EPS:
                    name = s.get(Item, item_id).name
                    raise BusinessError(f"المرتجع من {name} أكبر من المباع في الفاتورة الأصلية "
                                        f"({max(sold.get(item_id, 0.0), 0):g})")
        total = r2(sum(l.amount for l in clean))
        if discount < 0 or discount > total:
            raise BusinessError("الخصم لازم يكون بين صفر وإجمالي الفاتورة")
        if invoice_id is None:
            check_access(actor, SCREEN, "add")
            doc = SalesInvoice(created_by_id=actor.id)
        else:
            doc = s.get(SalesInvoice, invoice_id)
            if doc is None:
                raise BusinessError("المستند غير موجود")
            check_access(actor, SCREEN, "edit", doc)
            check_editable(doc)
            resubmit(doc)
        ensure_open(s, doc_date)
        doc.kind, doc.doc_date, doc.party_id, doc.payment = kind, doc_date, party_id, payment
        doc.treasury_account_id, doc.discount = treasury_account_id, discount
        doc.sales_order_id, doc.return_of_id = sales_order_id or None, return_of_id
        doc.dispatch_no = (dispatch_no or "").strip() or None
        doc.memo = (memo or "").strip() or None
        doc.lines = clean
        s.add(doc)
        s.flush()
        audit.log(s, actor, "create" if invoice_id is None else "update", "sales_invoice", doc.id,
                  {"doc_no": doc.doc_no, "total": r2(total - discount)})
        return doc.id


def delete_invoice(actor, invoice_id: int) -> None:
    with session_scope() as s:
        doc = s.get(SalesInvoice, invoice_id)
        if doc is None:
            return
        check_access(actor, SCREEN, "delete", doc)
        check_editable(doc)
        audit.log(s, actor, "delete", "sales_invoice", doc.id, {"doc_no": doc.doc_no})
        s.delete(doc)


def get_invoice(invoice_id: int) -> dict:
    with session_scope() as s:
        d = s.get(SalesInvoice, invoice_id)
        if d is None:
            raise BusinessError("المستند غير موجود")
        return {"id": d.id, "doc_no": d.doc_no, "kind": d.kind, "doc_date": d.doc_date, "party_id": d.party_id,
                "payment": d.payment, "treasury_account_id": d.treasury_account_id, "discount": float(d.discount or 0),
                "sales_order_id": d.sales_order_id, "return_of_id": d.return_of_id, "dispatch_no": d.dispatch_no or "",
                "memo": d.memo or "", "status": d.status, "rejection_note": d.rejection_note,
                "lines": [{"item_id": l.item_id, "mold_id": l.mold_id, "order_line_id": l.order_line_id,
                           "qty": float(l.qty), "unit_price": float(l.unit_price),
                           "description": l.description or ""} for l in d.lines]}


def invoices_frame(actor, kind: str | None = None, party_id: int | None = None) -> pd.DataFrame:
    with session_scope() as s:
        names = dict(s.execute(select(Party.id, Party.name)).all())
        users = dict(s.execute(select(User.id, User.full_name)).all())
        q = select(SalesInvoice).order_by(SalesInvoice.doc_date.desc(), SalesInvoice.id.desc())
        if kind:
            q = q.where(SalesInvoice.kind == kind)
        if party_id:
            q = q.where(SalesInvoice.party_id == party_id)
        if own_only(actor, SCREEN):
            q = q.where(SalesInvoice.created_by_id == actor.id)
        rows = []
        for d in s.scalars(q):
            gross = r2(sum(l.amount for l in d.lines))
            rows.append({"id": d.id, "رقم المستند": d.doc_no, "النوع": SALE_KINDS[d.kind], "التاريخ": d.doc_date,
                         "العميل": names.get(d.party_id, ""), "أمر العميل": f"SO-{d.sales_order_id:06d}"
                         if d.sales_order_id else "", "رقم الإذن": d.dispatch_no or "",
                         "السداد": PAYMENT_TERMS[d.payment], "الإجمالي": gross, "الخصم": float(d.discount or 0),
                         "الصافي": r2(gross - float(d.discount or 0)), "الحالة": status_label(d.status),
                         "ملاحظة المراجع": d.rejection_note or "", "سجّله": users.get(d.created_by_id, "")})
    return pd.DataFrame(rows, columns=["id", "رقم المستند", "النوع", "التاريخ", "العميل", "أمر العميل", "رقم الإذن",
                                       "السداد", "الإجمالي", "الخصم", "الصافي", "الحالة", "ملاحظة المراجع", "سجّله"])


def invoice_options(party_id: int) -> dict[int, str]:
    """Approved invoices of a customer, for a return to point at."""
    with session_scope() as s:
        q = (select(SalesInvoice).where(SalesInvoice.party_id == party_id, SalesInvoice.kind == SALE_INVOICE,
                                        SalesInvoice.status == APPROVED)
             .order_by(SalesInvoice.doc_date.desc(), SalesInvoice.id.desc()))
        return {d.id: f"{d.doc_no} - {d.doc_date}" for d in s.scalars(q)}


def order_lines_for_invoice(order_id: int) -> list[dict]:
    """The order's remaining quantities, ready to pull into an invoice."""
    df = order_progress(order_id)
    return [{"item_id": int(r["item_id"]), "order_line_id": int(r["line_id"]), "qty": float(r["الباقي"]),
             "unit_price": float(r["السعر"]), "description": ""} for _, r in df.iterrows() if r["الباقي"] > EPS]


def lines_report(date_from: date, date_to: date) -> pd.DataFrame:
    """Approved sales lines with revenue and cost, returns negative."""
    with session_scope() as s:
        names = dict(s.execute(select(Party.id, Party.name)).all())
        items = {i.id: i for i in s.scalars(select(Item))}
        molds = {m.id: m for m in s.scalars(select(Mold))}
        q = (select(SalesInvoice).where(SalesInvoice.status == APPROVED, SalesInvoice.doc_date >= date_from,
                                        SalesInvoice.doc_date <= date_to)
             .order_by(SalesInvoice.doc_date, SalesInvoice.id))
        rows = []
        for d in s.scalars(q):
            sign = -1 if d.kind == SALE_RETURN else 1
            nets = _net_amounts(d)
            for line, net in zip(d.lines, nets):
                it = items.get(line.item_id)
                cost = r2(float(line.qty) * float(line.unit_cost or 0))
                rows.append({"التاريخ": d.doc_date, "الشهر": d.doc_date.strftime("%Y-%m"), "المستند": d.doc_no,
                             "العميل": names.get(d.party_id, ""),
                             "الصنف": it.name if it else f"أسطمبة {molds[line.mold_id].code} {molds[line.mold_id].name}",
                             "الوحدة": it.unit if it else "أسطمبة", "الكمية": sign * float(line.qty),
                             "السعر": float(line.unit_price), "صافي المبيعات": sign * net, "التكلفة": sign * cost,
                             "مجمل الربح": sign * r2(net - cost)})
    return pd.DataFrame(rows, columns=["التاريخ", "الشهر", "المستند", "العميل", "الصنف", "الوحدة", "الكمية", "السعر",
                                       "صافي المبيعات", "التكلفة", "مجمل الربح"])


def _net_amounts(doc: SalesInvoice) -> list[float]:
    """Each line's amount after its share of the invoice discount."""
    gross = [l.amount for l in doc.lines]
    total, discount = sum(gross), float(doc.discount or 0)
    if not discount or not total:
        return gross
    shares = [r2(discount * g / total) for g in gross]
    shares[-1] = r2(discount - sum(shares[:-1]))
    return [r2(g - sh) for g, sh in zip(gross, shares)]


# ---------------------------------------------------------------- approval hooks

def _return_costs(session, doc: SalesInvoice) -> dict[int, float]:
    """Unit cost per item on the invoice a return reverses."""
    if not doc.return_of_id:
        return {}
    original = session.get(SalesInvoice, doc.return_of_id)
    return {l.item_id: float(l.unit_cost) for l in original.lines if l.item_id and l.unit_cost is not None}


def _plan(session, doc: SalesInvoice) -> list[stock.PlannedMove]:
    item_lines = [l for l in doc.lines if l.item_id]
    if doc.kind == SALE_INVOICE:
        return stock.plan_outbound(session, [(l.item_id, float(l.qty), l.description) for l in item_lines])
    costs = _return_costs(session, doc)
    return [stock.PlannedMove(l.item_id, qty_in=float(l.qty),
                              unit_cost=round(costs.get(l.item_id, stock.average_cost(session, l.item_id)), 6),
                              memo=l.description) for l in item_lines]


def _check_mold_sale(session, doc: SalesInvoice, m: Mold) -> None:
    pending = session.scalar(select(MoldAmortization).where(MoldAmortization.status != APPROVED,
                                                            MoldAmortization.doc_date <= doc.doc_date).limit(1))
    if pending is not None:
        raise BusinessError(f"اعتمد إهلاك أسطمبات شهر {pending.month}/{pending.year} الأول قبل بيع الأسطمبة")
    later = session.scalar(select(MoldAmortization).where(
        (MoldAmortization.year > doc.doc_date.year)
        | ((MoldAmortization.year == doc.doc_date.year) & (MoldAmortization.month > doc.doc_date.month))).limit(1))
    if later is not None:
        raise BusinessError("فيه إهلاك أسطمبات محسوب لشهر بعد تاريخ البيع؛ احذفه الأول")


def _build_lines(session, doc: SalesInvoice) -> list[PostingLine]:
    memo = doc.memo or SALE_KINDS[doc.kind]
    gross_items = r2(sum(l.amount for l in doc.lines if l.item_id))
    gross_molds = r2(sum(l.amount for l in doc.lines if l.mold_id))
    net = r2(gross_items + gross_molds - float(doc.discount or 0))
    if doc.payment == CASH:
        party_line = PostingLine(doc.treasury_account_id, memo=memo)
    else:
        party_line = PostingLine(_account(session, "ar"), memo=memo, party_id=doc.party_id)
    moves = _plan(session, doc)
    by_acc: dict[int, float] = {}
    for m in moves:
        acc = stock.group_account(session, m.item_id)
        by_acc[acc] = r2(by_acc.get(acc, 0) + m.value)
    cost = r2(sum(by_acc.values()))
    cogs = _account(session, "cogs")
    lines: list[PostingLine] = []
    if doc.kind == SALE_INVOICE:
        party_line.debit = net
        lines.append(party_line)
        if doc.discount:
            lines.append(PostingLine(_account(session, "discount_allowed"), debit=float(doc.discount), memo=memo))
        if gross_items:
            lines.append(PostingLine(_account(session, "sales"), credit=gross_items, memo=memo))
        if gross_molds:
            lines.append(PostingLine(_account(session, "sales_molds"), credit=gross_molds, memo=memo))
        if cost:
            lines.append(PostingLine(cogs, debit=cost, memo=f"تكلفة {memo}"))
            lines += [PostingLine(a, credit=v, memo=f"تكلفة {memo}") for a, v in by_acc.items()]
        for l in doc.lines:
            if not l.mold_id:
                continue
            m = session.get(Mold, l.mold_id)
            mcost = mold_reg.cost_total(session, m, doc.doc_date)
            macc = mold_reg.accumulated(session, m, doc.doc_date)
            mm = f"تكلفة أسطمبة مباعة {m.code} {m.name}"
            if mcost:
                lines.append(PostingLine(_account(session, "mold_asset"), credit=mcost, memo=mm))
                if macc:
                    lines.append(PostingLine(_account(session, "mold_acc_amortization"), debit=macc, memo=mm))
                if r2(mcost - macc) > 0:
                    lines.append(PostingLine(_account(session, "cogs_molds"), debit=r2(mcost - macc), memo=mm))
    else:
        lines.append(PostingLine(_account(session, "sales_returns"), debit=gross_items, memo=memo))
        party_line.credit = net
        lines.append(party_line)
        if cost:
            lines += [PostingLine(a, debit=v, memo=f"تكلفة {memo}") for a, v in by_acc.items()]
            lines.append(PostingLine(cogs, credit=cost, memo=f"تكلفة {memo}"))
    return lines


def _on_post(session, doc: SalesInvoice, entry_id) -> None:
    for l in doc.lines:
        if l.mold_id:
            m = session.get(Mold, l.mold_id)
            if m.status != ACTIVE:
                raise BusinessError(f"الأسطمبة {m.code} خارج الخدمة أو اتباعت")
            _check_mold_sale(session, doc, m)
            m.status = RETIRED
    moves = _plan(session, doc)
    stock.write_moves(session, doc, "sales_invoice", "sale" if doc.kind == SALE_INVOICE else "sale_return", moves,
                      doc.party_id)
    item_lines = [l for l in doc.lines if l.item_id]
    for line, m in zip(item_lines, moves):
        line.unit_cost = m.unit_cost


def _on_void(session, doc: SalesInvoice) -> None:
    if doc.kind == SALE_INVOICE:
        later = session.scalar(select(SalesInvoice.id).where(SalesInvoice.return_of_id == doc.id,
                                                             SalesInvoice.status == APPROVED).limit(1))
        if later:
            raise BusinessError("فيه مرتجع معتمد على الفاتورة دي؛ الغي اعتماده الأول")
    stock.void_moves(session, "sales_invoice", doc.id)
    for l in doc.lines:
        l.unit_cost = None
        if l.mold_id:
            session.get(Mold, l.mold_id).status = ACTIVE


def _describe(session, doc: SalesInvoice):
    party = session.get(Party, doc.party_id)
    total = r2(sum(l.amount for l in doc.lines) - float(doc.discount or 0))
    text = " - ".join(x for x in (party.name if party else "", PAYMENT_TERMS[doc.payment], doc.memo) if x)
    return SALE_KINDS[doc.kind], text, total


approval.register(approval.DocType("sales_invoice", SalesInvoice, SCREEN, _describe, _build_lines,
                                   on_post=_on_post, on_void=_on_void))


# ---------------------------------------------------------------- aging

AGING_BUCKETS = ((0, 30, "0-30 يوم"), (31, 60, "31-60 يوم"), (61, 90, "61-90 يوم"), (91, None, "أكتر من 90 يوم"))


def aging_frame(as_of: date) -> pd.DataFrame:
    """Customer balances by age: what is owed is matched to the newest debits (payments clear the oldest first)."""
    from core.models import JournalEntry, JournalLine
    from core.models.ledger import ENTRY_POSTED

    with session_scope() as s:
        ar = _account(s, "ar")
        names = {p.id: p.name for p in s.scalars(select(Party))}
        q = (select(JournalLine.party_id, JournalEntry.entry_date, JournalLine.debit, JournalLine.credit)
             .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
             .where(JournalLine.account_id == ar, JournalEntry.status == ENTRY_POSTED, JournalEntry.entry_date <= as_of)
             .order_by(JournalEntry.entry_date, JournalEntry.id))
        debits: dict[int, list[tuple[date, float]]] = {}
        balance: dict[int, float] = {}
        for pid, d, dr, cr in s.execute(q):
            balance[pid] = balance.get(pid, 0.0) + float(dr or 0) - float(cr or 0)
            if dr:
                debits.setdefault(pid, []).append((d, float(dr)))
    rows = []
    for pid, bal in balance.items():
        bal = r2(bal)
        if abs(bal) < 0.005:
            continue
        row = {"العميل": names.get(pid, ""), "الرصيد": bal} | {b[2]: 0.0 for b in AGING_BUCKETS}
        left = bal
        for d, amt in reversed(debits.get(pid, [])):
            if left <= 0:
                break
            take = min(amt, left)
            age = (as_of - d).days
            for lo, hi, label in AGING_BUCKETS:
                if age >= lo and (hi is None or age <= hi):
                    row[label] = r2(row[label] + take)
                    break
            left = r2(left - take)
        if left > 0:  # opening balances with no dated debit
            row[AGING_BUCKETS[-1][2]] = r2(row[AGING_BUCKETS[-1][2]] + left)
        rows.append(row)
    cols = ["العميل", "الرصيد"] + [b[2] for b in AGING_BUCKETS]
    df = pd.DataFrame(rows, columns=cols)
    return df.sort_values("الرصيد", ascending=False) if not df.empty else df
