"""Approval engine: pending documents become balanced journal entries only when approved.

Each document type registers how to describe itself and which journal lines it posts.
Approving creates the entry; returning sends the document back to its author with a
mandatory note; un-approving voids the entry (kept for audit) and makes the document
editable again.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pandas as pd
from sqlalchemy import select

from core.db import session_scope
from core.models import Account, JournalEntry, JournalLine, Notification, User
from core.models.documents import APPROVED, PENDING, RETURNED
from core.models.ledger import ENTRY_POSTED, ENTRY_VOID
from core.services import audit
from core.services.errors import BusinessError
from core.services.periods import ensure_open

APPROVAL_SCREEN = "approvals"


@dataclass
class PostingLine:
    account_id: int
    debit: float = 0.0
    credit: float = 0.0
    memo: str | None = None


@dataclass
class DocType:
    key: str
    model: type
    screen_key: str
    # (type label, description, amount) for the approvals table
    describe: Callable
    # session, doc -> list[PostingLine]
    build_lines: Callable


REGISTRY: dict[str, DocType] = {}


def register(doc_type: DocType) -> None:
    REGISTRY[doc_type.key] = doc_type


def r2(x) -> float:
    return round(float(x or 0) + 0.0, 2)


def validate_lines(session, lines: list[PostingLine]) -> None:
    """Shared rules for anything that will become a journal entry."""
    if len(lines) < 2:
        raise BusinessError("القيد لازم يكون فيه سطرين على الأقل")
    total_dr = total_cr = 0.0
    for i, line in enumerate(lines, start=1):
        dr, cr = r2(line.debit), r2(line.credit)
        if dr < 0 or cr < 0:
            raise BusinessError(f"السطر {i}: لا يقبل قيم سالبة")
        if (dr > 0) == (cr > 0):
            raise BusinessError(f"السطر {i}: لازم يكون مدين أو دائن (واحد بس)")
        acc = session.get(Account, line.account_id)
        if acc is None:
            raise BusinessError(f"السطر {i}: الحساب غير موجود")
        if not acc.is_postable:
            raise BusinessError(f"السطر {i}: الحساب {acc.code} - {acc.name_ar} حساب رئيسي ولا يقبل قيود")
        if not acc.is_active:
            raise BusinessError(f"السطر {i}: الحساب {acc.code} - {acc.name_ar} موقوف")
        total_dr += dr
        total_cr += cr
    if r2(total_dr) != r2(total_cr):
        raise BusinessError(f"القيد غير متزن: المدين {total_dr:,.2f} والدائن {total_cr:,.2f}")
    if r2(total_dr) == 0:
        raise BusinessError("قيمة القيد صفر")


def _doc(session, doc_type: str, doc_id: int):
    dt = REGISTRY.get(doc_type)
    if dt is None:
        raise BusinessError("نوع مستند غير معروف")
    doc = session.get(dt.model, doc_id)
    if doc is None:
        raise BusinessError("المستند غير موجود")
    return dt, doc


def _notify(session, user_id: int, doc_type: str, doc, title: str, message: str | None, sender) -> None:
    session.add(Notification(user_id=user_id, doc_type=doc_type, doc_id=doc.id, doc_no=doc.doc_no,
                             title=title, message=message, sender=getattr(sender, "full_name", None)))


def approve(actor, doc_type: str, doc_id: int) -> int:
    if not actor.can(APPROVAL_SCREEN, "approve"):
        raise BusinessError("ليس لديك صلاحية الاعتماد")
    with session_scope() as s:
        dt, doc = _doc(s, doc_type, doc_id)
        if doc.status != PENDING:
            raise BusinessError(f"{doc.doc_no}: المستند ليس في انتظار الاعتماد")
        ensure_open(s, doc.doc_date)
        lines = dt.build_lines(s, doc)
        validate_lines(s, lines)
        entry = JournalEntry(
            entry_date=doc.doc_date, memo=doc.memo, source_type=doc_type, source_id=doc.id,
            source_no=doc.doc_no, status=ENTRY_POSTED, posted_by_id=actor.id,
            lines=[JournalLine(account_id=l.account_id, debit=r2(l.debit), credit=r2(l.credit), memo=l.memo)
                   for l in lines],
        )
        s.add(entry)
        s.flush()
        doc.status = APPROVED
        doc.journal_entry_id = entry.id
        doc.approved_by_id = actor.id
        doc.approved_at = datetime.now()
        doc.rejection_note = None
        audit.log(s, actor, "approve", doc_type, doc.id, {"doc_no": doc.doc_no, "entry": entry.entry_no})
        return entry.id


def return_for_correction(actor, doc_type: str, doc_id: int, note: str) -> None:
    if not actor.can(APPROVAL_SCREEN, "approve"):
        raise BusinessError("ليس لديك صلاحية الاعتماد أو الإرجاع")
    note = (note or "").strip()
    if not note:
        raise BusinessError("لازم تكتب ملاحظة الرفض")
    with session_scope() as s:
        _, doc = _doc(s, doc_type, doc_id)
        if doc.status != PENDING:
            raise BusinessError(f"{doc.doc_no}: المستند ليس في انتظار الاعتماد")
        doc.status = RETURNED
        doc.rejection_note = note
        doc.returned_by_id = actor.id
        doc.returned_at = datetime.now()
        _notify(s, doc.created_by_id, doc_type, doc, f"{doc.doc_no} مرتجع للتعديل", note, actor)
        audit.log(s, actor, "return", doc_type, doc.id, {"doc_no": doc.doc_no, "note": note})


def unapprove(actor, doc_type: str, doc_id: int, reason: str | None = None) -> None:
    if not actor.can(APPROVAL_SCREEN, "unapprove"):
        raise BusinessError("ليس لديك صلاحية إلغاء الاعتماد")
    with session_scope() as s:
        _, doc = _doc(s, doc_type, doc_id)
        if doc.status != APPROVED:
            raise BusinessError(f"{doc.doc_no}: المستند غير معتمد")
        ensure_open(s, doc.doc_date)
        entry = s.get(JournalEntry, doc.journal_entry_id)
        if entry is not None:
            entry.status = ENTRY_VOID
            entry.voided_by_id = actor.id
            entry.voided_at = datetime.now()
        doc.status = PENDING
        doc.journal_entry_id = None
        doc.approved_by_id = None
        doc.approved_at = None
        msg = (reason or "").strip() or None
        _notify(s, doc.created_by_id, doc_type, doc, f"تم إلغاء اعتماد {doc.doc_no}", msg, actor)
        audit.log(s, actor, "unapprove", doc_type, doc.id,
                  {"doc_no": doc.doc_no, "voided_entry": entry.entry_no if entry else None, "reason": msg})


def run_many(fn, actor, items: list[tuple[str, int]], *args) -> tuple[int, list[str]]:
    """Apply an action to several documents; each succeeds or fails on its own."""
    done, errors = 0, []
    for doc_type, doc_id in items:
        try:
            fn(actor, doc_type, doc_id, *args)
            done += 1
        except BusinessError as exc:
            errors.append(str(exc))
    return done, errors


def documents_frame(statuses: tuple[str, ...], date_from=None, date_to=None) -> pd.DataFrame:
    """Documents of every registered type in the given statuses, for the approvals screen."""
    rows = []
    with session_scope() as s:
        names = dict(s.execute(select(User.id, User.full_name)).all())
        for key, dt in REGISTRY.items():
            q = select(dt.model).where(dt.model.status.in_(statuses))
            if date_from:
                q = q.where(dt.model.doc_date >= date_from)
            if date_to:
                q = q.where(dt.model.doc_date <= date_to)
            for doc in s.scalars(q):
                label, description, amount = dt.describe(s, doc)
                rows.append({
                    "ref": f"{key}:{doc.id}", "التاريخ": doc.doc_date, "نوع الحركة": label, "رقم المستند": doc.doc_no,
                    "البيان": description, "القيمة": amount, "سجّلها": names.get(doc.created_by_id, ""),
                    "وقت التسجيل": doc.created_at,
                    "اعتمدها": names.get(doc.approved_by_id, "") if doc.approved_by_id else "",
                })
    cols = ["ref", "التاريخ", "نوع الحركة", "رقم المستند", "البيان", "القيمة", "سجّلها", "وقت التسجيل", "اعتمدها"]
    df = pd.DataFrame(rows, columns=cols)
    return df.sort_values(["التاريخ", "وقت التسجيل"]).reset_index(drop=True) if len(df) else df


def preview(doc_type: str, doc_id: int) -> pd.DataFrame:
    """The journal lines approval would post (or did post)."""
    with session_scope() as s:
        dt, doc = _doc(s, doc_type, doc_id)
        lines = dt.build_lines(s, doc)
        accounts = {a.id: a for a in s.scalars(select(Account).where(Account.id.in_([l.account_id for l in lines])))}
        return pd.DataFrame([
            {"الحساب": f"{accounts[l.account_id].code} - {accounts[l.account_id].name_ar}" if l.account_id in accounts else "؟",
             "مدين": r2(l.debit), "دائن": r2(l.credit), "البيان": l.memo or ""}
            for l in lines
        ])


def parse_ref(ref: str) -> tuple[str, int]:
    key, _, doc_id = ref.partition(":")
    return key, int(doc_id)
