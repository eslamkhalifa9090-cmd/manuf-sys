"""Purchase invoices and returns.

Item lines go to stock at their net cost (after the invoice discount is spread over the
lines by value). Direct-account lines (fixed assets, expenses) post straight to that account.
Returns take items out at the current average; any gap to the supplier's return price goes
to cost variances (5404).
"""
from datetime import date

import pandas as pd
from sqlalchemy import select

from core.db import session_scope
from core.models import Account, Item, Party, PurchaseInvoice, PurchaseLine, User
from core.models.inventory import CASH, CREDIT, PAYMENT_TERMS, PURCHASE_INVOICE, PURCHASE_KINDS, PURCHASE_RETURN
from core.models.parties import BOTH, SUPPLIER
from core.services import approval, audit, stock
from core.services.approval import PostingLine, r2
from core.services.doc_rules import check_access, check_editable, own_only, resubmit, status_label
from core.services.errors import BusinessError
from core.services.periods import ensure_open

SCREEN = "purchases"
TREASURY_PREFIX = "15"


def _system_account(session, key: str) -> int:
    acc_id = session.scalar(select(Account.id).where(Account.system_key == key))
    if acc_id is None:
        raise BusinessError(f"حساب النظام {key} غير موجود")
    return acc_id


def net_amounts(lines: list[PurchaseLine], discount: float) -> list[float]:
    """Each line's amount after its share of the invoice discount; the last line absorbs rounding."""
    gross = [l.amount for l in lines]
    total = r2(sum(gross))
    discount = r2(discount)
    if not discount or not total:
        return gross
    shares = [r2(discount * g / total) for g in gross]
    shares[-1] = r2(discount - sum(shares[:-1]))
    return [r2(g - sh) for g, sh in zip(gross, shares)]


def save_invoice(actor, invoice_id: int | None, kind: str, doc_date: date, party_id: int, payment: str,
                 lines: list[dict], discount: float = 0, treasury_account_id: int | None = None,
                 supplier_ref: str = "", memo: str = "") -> int:
    """lines: dicts with item_id or account_id, qty, unit_price, description."""
    if kind not in PURCHASE_KINDS:
        raise BusinessError("نوع المستند غير معروف")
    if payment not in PAYMENT_TERMS:
        raise BusinessError("طريقة السداد غير معروفة")
    if doc_date is None:
        raise BusinessError("التاريخ مطلوب")
    with session_scope() as s:
        party = s.get(Party, party_id) if party_id else None
        if party is None or party.kind not in (SUPPLIER, BOTH):
            raise BusinessError("اختار المورد")
        if payment == CASH:
            treasury = s.get(Account, treasury_account_id) if treasury_account_id else None
            if treasury is None or not treasury.code.startswith(TREASURY_PREFIX) or not treasury.is_postable:
                raise BusinessError("الشراء النقدي محتاج تختار الخزينة أو المحفظة أو البنك")
        else:
            treasury_account_id = None
        clean = []
        for i, l in enumerate(lines, start=1):
            item_id, account_id = l.get("item_id"), l.get("account_id")
            qty, price = float(l.get("qty") or 0), float(l.get("unit_price") or 0)
            if not item_id and not account_id and not qty and not price:
                continue
            if bool(item_id) == bool(account_id):
                raise BusinessError(f"السطر {i}: اختار صنف أو حساب (واحد بس)")
            if qty <= 0:
                raise BusinessError(f"السطر {i}: الكمية لازم تكون أكبر من صفر")
            if price < 0:
                raise BusinessError(f"السطر {i}: السعر مينفعش يكون سالب")
            if item_id and s.get(Item, item_id) is None:
                raise BusinessError(f"السطر {i}: الصنف غير موجود")
            if account_id:
                acc = s.get(Account, account_id)
                if acc is None or not acc.is_postable or not acc.is_active:
                    raise BusinessError(f"السطر {i}: اختار حساب فرعي نشط")
                if acc.system_key in approval.PARTY_ACCOUNTS or acc.code.startswith("13"):
                    raise BusinessError(f"السطر {i}: الأصناف المخزنية تتسجل كصنف مش كحساب")
            clean.append(PurchaseLine(item_id=item_id or None, account_id=account_id or None, qty=qty,
                                      unit_price=price, description=(l.get("description") or "").strip() or None))
        if not clean:
            raise BusinessError("الفاتورة لازم يكون فيها سطر واحد على الأقل")
        total = r2(sum(l.amount for l in clean))
        discount = r2(discount or 0)
        if discount < 0 or discount > total:
            raise BusinessError("الخصم لازم يكون بين صفر وإجمالي الفاتورة")
        if r2(total - discount) <= 0:
            raise BusinessError("صافي الفاتورة صفر")
        if invoice_id is None:
            check_access(actor, SCREEN, "add")
            doc = PurchaseInvoice(created_by_id=actor.id)
        else:
            doc = s.get(PurchaseInvoice, invoice_id)
            if doc is None:
                raise BusinessError("المستند غير موجود")
            check_access(actor, SCREEN, "edit", doc)
            check_editable(doc)
            resubmit(doc)
        ensure_open(s, doc_date)
        doc.kind, doc.doc_date, doc.party_id, doc.payment = kind, doc_date, party_id, payment
        doc.treasury_account_id, doc.discount = treasury_account_id, discount
        doc.supplier_ref, doc.memo = (supplier_ref or "").strip() or None, (memo or "").strip() or None
        doc.lines = clean
        s.add(doc)
        s.flush()
        audit.log(s, actor, "create" if invoice_id is None else "update", "purchase_invoice", doc.id,
                  {"doc_no": doc.doc_no, "net": r2(total - discount)})
        return doc.id


def delete_invoice(actor, invoice_id: int) -> None:
    with session_scope() as s:
        doc = s.get(PurchaseInvoice, invoice_id)
        if doc is None:
            return
        check_access(actor, SCREEN, "delete", doc)
        check_editable(doc)
        audit.log(s, actor, "delete", "purchase_invoice", doc.id, {"doc_no": doc.doc_no})
        s.delete(doc)


def get_invoice(invoice_id: int) -> dict:
    with session_scope() as s:
        d = s.get(PurchaseInvoice, invoice_id)
        if d is None:
            raise BusinessError("المستند غير موجود")
        return {
            "id": d.id, "doc_no": d.doc_no, "kind": d.kind, "doc_date": d.doc_date, "party_id": d.party_id,
            "payment": d.payment, "treasury_account_id": d.treasury_account_id, "discount": d.discount,
            "supplier_ref": d.supplier_ref or "", "memo": d.memo or "", "status": d.status,
            "rejection_note": d.rejection_note, "created_by_id": d.created_by_id,
            "lines": [{"item_id": l.item_id, "account_id": l.account_id, "qty": l.qty, "unit_price": l.unit_price,
                       "description": l.description or ""} for l in d.lines],
        }


def invoices_frame(actor, kind: str | None = None, party_id: int | None = None) -> pd.DataFrame:
    with session_scope() as s:
        names = dict(s.execute(select(User.id, User.full_name)).all())
        parties = {p.id: p.name for p in s.scalars(select(Party))}
        q = select(PurchaseInvoice).order_by(PurchaseInvoice.doc_date.desc(), PurchaseInvoice.id.desc())
        if kind:
            q = q.where(PurchaseInvoice.kind == kind)
        if party_id:
            q = q.where(PurchaseInvoice.party_id == party_id)
        if own_only(actor, SCREEN):
            q = q.where(PurchaseInvoice.created_by_id == actor.id)
        rows = []
        for d in s.scalars(q):
            total = r2(sum(l.amount for l in d.lines))
            rows.append({"id": d.id, "رقم المستند": d.doc_no, "التاريخ": d.doc_date, "النوع": PURCHASE_KINDS[d.kind],
                         "المورد": parties.get(d.party_id, ""), "رقم فاتورة المورد": d.supplier_ref or "",
                         "السداد": PAYMENT_TERMS[d.payment], "الإجمالي": total, "الخصم": d.discount or 0.0,
                         "الصافي": r2(total - (d.discount or 0)), "الحالة": status_label(d.status),
                         "ملاحظة المراجع": d.rejection_note or "", "سجّله": names.get(d.created_by_id, "")})
    return pd.DataFrame(rows, columns=["id", "رقم المستند", "التاريخ", "النوع", "المورد", "رقم فاتورة المورد", "السداد",
                                       "الإجمالي", "الخصم", "الصافي", "الحالة", "ملاحظة المراجع", "سجّله"])


def lines_report(date_from: date, date_to: date) -> pd.DataFrame:
    """Approved purchase lines for analysis and export."""
    from core.models.documents import APPROVED

    with session_scope() as s:
        parties = {p.id: p.name for p in s.scalars(select(Party))}
        items = {i.id: (i.code, i.name, i.unit) for i in s.scalars(select(Item))}
        accounts = {a.id: f"{a.code} - {a.name_ar}" for a in s.scalars(select(Account))}
        q = (select(PurchaseInvoice).where(PurchaseInvoice.status == APPROVED, PurchaseInvoice.doc_date >= date_from,
                                           PurchaseInvoice.doc_date <= date_to)
             .order_by(PurchaseInvoice.doc_date, PurchaseInvoice.id))
        rows = []
        for d in s.scalars(q):
            sign = -1 if d.kind == PURCHASE_RETURN else 1
            for line, net in zip(d.lines, net_amounts(d.lines, d.discount or 0)):
                code, name, unit = items.get(line.item_id, ("", "", ""))
                rows.append({"التاريخ": d.doc_date, "المستند": d.doc_no, "المورد": parties.get(d.party_id, ""),
                             "الصنف / الحساب": name or accounts.get(line.account_id, ""), "كود الصنف": code,
                             "الوحدة": unit, "الكمية": sign * line.qty, "السعر": line.unit_price,
                             "الصافي": sign * net, "البيان": line.description or ""})
    return pd.DataFrame(rows, columns=["التاريخ", "المستند", "المورد", "الصنف / الحساب", "كود الصنف", "الوحدة", "الكمية",
                                       "السعر", "الصافي", "البيان"])


# ---------- approval hooks ----------

def _plan(session, doc: PurchaseInvoice):
    """Stock moves and the debit (or credit, for returns) per account."""
    nets = net_amounts(doc.lines, doc.discount or 0)
    moves, by_account = [], {}
    if doc.kind == PURCHASE_INVOICE:
        for line, net in zip(doc.lines, nets):
            if line.item_id:
                moves.append(stock.PlannedMove(line.item_id, qty_in=line.qty, unit_cost=round(net / line.qty, 6),
                                               memo=line.description))
                acc = stock.group_account(session, line.item_id)
            else:
                acc = line.account_id
            by_account[acc] = r2(by_account.get(acc, 0) + net)
        variance = 0.0
    else:
        item_lines = [(l, n) for l, n in zip(doc.lines, nets) if l.item_id]
        moves = stock.plan_outbound(session, [(l.item_id, l.qty, l.description) for l, _ in item_lines])
        for (line, _), m in zip(item_lines, moves):
            acc = stock.group_account(session, line.item_id)
            by_account[acc] = r2(by_account.get(acc, 0) + m.value)
        for line, net in zip(doc.lines, nets):
            if line.account_id:
                by_account[line.account_id] = r2(by_account.get(line.account_id, 0) + net)
        # Supplier credit at the return price; stock leaves at average. The gap is a cost variance.
        variance = r2(sum(nets) - sum(by_account.values()))
    return moves, by_account, r2(sum(nets)), variance


def _build_lines(session, doc: PurchaseInvoice) -> list[PostingLine]:
    _, by_account, net_total, variance = _plan(session, doc)
    memo = doc.memo or PURCHASE_KINDS[doc.kind]
    if doc.payment == CASH:
        other = PostingLine(doc.treasury_account_id, memo=memo)
    else:
        other = PostingLine(_system_account(session, "ap"), memo=memo, party_id=doc.party_id)
    lines = []
    if doc.kind == PURCHASE_INVOICE:
        lines += [PostingLine(acc, debit=v, memo=memo) for acc, v in by_account.items()]
        other.credit = net_total
        lines.append(other)
    else:
        other.debit = net_total
        lines.append(other)
        lines += [PostingLine(acc, credit=v, memo=memo) for acc, v in by_account.items()]
        if variance:
            var_acc = _system_account(session, "cost_variance")
            lines.append(PostingLine(var_acc, debit=-variance if variance < 0 else 0,
                                     credit=variance if variance > 0 else 0, memo="فرق سعر مرتجع عن متوسط التكلفة"))
    return lines


def _on_post(session, doc: PurchaseInvoice, entry_id) -> None:
    moves, *_ = _plan(session, doc)
    stock.write_moves(session, doc, "purchase_invoice", "purchase" if doc.kind == PURCHASE_INVOICE else "purchase_return",
                      moves, doc.party_id)


def _describe(session, doc: PurchaseInvoice):
    party = session.get(Party, doc.party_id)
    total = r2(sum(l.amount for l in doc.lines) - (doc.discount or 0))
    text = " - ".join(x for x in (party.name if party else "", PAYMENT_TERMS[doc.payment], doc.memo) if x)
    return PURCHASE_KINDS[doc.kind], text, total


approval.register(approval.DocType("purchase_invoice", PurchaseInvoice, SCREEN, _describe, _build_lines,
                                   on_post=_on_post, on_void=lambda s, d: stock.void_moves(s, "purchase_invoice", d.id)))
