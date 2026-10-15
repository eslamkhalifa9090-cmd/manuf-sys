"""Receipt and payment vouchers on cash, wallets (Vodafone Cash, InstaPay) and bank."""
from datetime import date

import pandas as pd
from sqlalchemy import select

from core.db import session_scope
from core.models import Account, CashVoucher, User
from core.models.documents import PAYMENT, RECEIPT, VOUCHER_KINDS
from core.services import approval, audit
from core.services.approval import PostingLine, r2, validate_lines
from core.services.doc_rules import check_access, check_editable, own_only, resubmit, status_label
from core.services.errors import BusinessError
from core.services.periods import ensure_open

SCREEN = "cash_vouchers"
TREASURY_PREFIX = "15"  # النقدية وما في حكمها


def treasury_accounts() -> dict[int, str]:
    with session_scope() as s:
        rows = s.scalars(select(Account).where(Account.code.startswith(TREASURY_PREFIX), Account.is_postable,
                                               Account.is_active).order_by(Account.code))
        return {a.id: f"{a.code} - {a.name_ar}" for a in rows}


def _lines(doc) -> list[PostingLine]:
    memo = doc.party_name or doc.memo
    if doc.kind == RECEIPT:
        return [PostingLine(doc.treasury_account_id, doc.amount, 0, memo),
                PostingLine(doc.counter_account_id, 0, doc.amount, memo)]
    return [PostingLine(doc.counter_account_id, doc.amount, 0, memo),
            PostingLine(doc.treasury_account_id, 0, doc.amount, memo)]


def save_voucher(actor, voucher_id: int | None, kind: str, doc_date: date, treasury_account_id: int,
                 counter_account_id: int, amount: float, party_name: str = "", reference: str = "",
                 memo: str = "") -> int:
    if kind not in VOUCHER_KINDS:
        raise BusinessError("نوع السند غير معروف")
    if doc_date is None:
        raise BusinessError("التاريخ مطلوب")
    amount = r2(amount)
    if amount <= 0:
        raise BusinessError("المبلغ لازم يكون أكبر من صفر")
    if treasury_account_id == counter_account_id:
        raise BusinessError("الحساب المقابل لازم يكون غير حساب الخزينة")
    with session_scope() as s:
        treasury = s.get(Account, treasury_account_id)
        if treasury is None or not treasury.code.startswith(TREASURY_PREFIX):
            raise BusinessError("اختار خزينة أو محفظة أو بنك من حسابات النقدية")
        if voucher_id is None:
            check_access(actor, SCREEN, "add")
            doc = CashVoucher(created_by_id=actor.id)
        else:
            doc = s.get(CashVoucher, voucher_id)
            if doc is None:
                raise BusinessError("السند غير موجود")
            check_access(actor, SCREEN, "edit", doc)
            check_editable(doc)
            resubmit(doc)
        ensure_open(s, doc_date)
        doc.kind, doc.doc_date, doc.amount = kind, doc_date, amount
        doc.treasury_account_id, doc.counter_account_id = treasury_account_id, counter_account_id
        doc.party_name = (party_name or "").strip() or None
        doc.reference = (reference or "").strip() or None
        doc.memo = (memo or "").strip() or None
        validate_lines(s, _lines(doc))
        s.add(doc)
        s.flush()
        audit.log(s, actor, "create" if voucher_id is None else "update", "cash_voucher", doc.id,
                  {"doc_no": doc.doc_no, "amount": amount})
        return doc.id


def delete_voucher(actor, voucher_id: int) -> None:
    with session_scope() as s:
        doc = s.get(CashVoucher, voucher_id)
        if doc is None:
            return
        check_access(actor, SCREEN, "delete", doc)
        check_editable(doc)
        audit.log(s, actor, "delete", "cash_voucher", doc.id, {"doc_no": doc.doc_no})
        s.delete(doc)


def get_voucher(voucher_id: int) -> dict:
    with session_scope() as s:
        d = s.get(CashVoucher, voucher_id)
        if d is None:
            raise BusinessError("السند غير موجود")
        return {c: getattr(d, c) for c in ("id", "kind", "doc_date", "treasury_account_id", "counter_account_id",
                                           "amount", "party_name", "reference", "memo", "status",
                                           "rejection_note", "created_by_id")} | {"doc_no": d.doc_no}


def vouchers_frame(actor, kind: str | None = None) -> pd.DataFrame:
    with session_scope() as s:
        names = dict(s.execute(select(User.id, User.full_name)).all())
        accounts = {a.id: f"{a.code} - {a.name_ar}" for a in s.scalars(select(Account))}
        q = select(CashVoucher).order_by(CashVoucher.doc_date.desc(), CashVoucher.id.desc())
        if kind:
            q = q.where(CashVoucher.kind == kind)
        if own_only(actor, SCREEN):
            q = q.where(CashVoucher.created_by_id == actor.id)
        rows = [{
            "id": d.id, "رقم السند": d.doc_no, "التاريخ": d.doc_date, "النوع": VOUCHER_KINDS[d.kind],
            "الخزينة": accounts.get(d.treasury_account_id, ""), "الحساب المقابل": accounts.get(d.counter_account_id, ""),
            "الجهة": d.party_name or "", "المبلغ": d.amount, "المرجع": d.reference or "", "البيان": d.memo or "",
            "الحالة": status_label(d.status), "ملاحظة المراجع": d.rejection_note or "",
            "سجّله": names.get(d.created_by_id, ""),
        } for d in s.scalars(q)]
    return pd.DataFrame(rows, columns=["id", "رقم السند", "التاريخ", "النوع", "الخزينة", "الحساب المقابل", "الجهة",
                                       "المبلغ", "المرجع", "البيان", "الحالة", "ملاحظة المراجع", "سجّله"])


def _describe(session, doc: CashVoucher):
    counter = session.get(Account, doc.counter_account_id)
    who = doc.party_name or (counter.name_ar if counter else "")
    text = " - ".join(x for x in (who, doc.memo) if x)
    return VOUCHER_KINDS[doc.kind], text, r2(doc.amount)


approval.register(approval.DocType("cash_voucher", CashVoucher, SCREEN, _describe, lambda s, d: _lines(d)))

__all__ = ["RECEIPT", "PAYMENT", "save_voucher", "delete_voucher", "vouchers_frame", "treasury_accounts"]
