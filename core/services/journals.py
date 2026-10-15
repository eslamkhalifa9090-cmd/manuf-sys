"""Manual journal vouchers and opening-balance entries."""
from datetime import date

import pandas as pd
from sqlalchemy import select

from core.db import session_scope
from core.models import Account, ManualJournal, ManualJournalLine, User
from core.models.documents import JOURNAL_KINDS, JOURNAL_OPENING, JOURNAL_REGULAR
from core.services import approval, audit
from core.services.approval import PostingLine, r2, validate_lines
from core.services.doc_rules import check_access, check_editable, own_only, resubmit, status_label
from core.services.errors import BusinessError
from core.services.periods import ensure_open

SCREEN = "manual_journals"
LINE_COLUMNS = ["كود الحساب", "مدين", "دائن", "البيان"]


def _opening_equity(session) -> Account:
    acc = session.scalar(select(Account).where(Account.system_key == "opening_balance_equity"))
    if acc is None:
        raise BusinessError("حساب أرصدة افتتاحية - حقوق ملكية غير موجود في الشجرة")
    return acc


def _clean_lines(lines: list[dict]) -> list[PostingLine]:
    out = []
    for line in lines:
        dr, cr = r2(line.get("debit")), r2(line.get("credit"))
        if not line.get("account_id") and dr == 0 and cr == 0:
            continue  # empty editor row
        if not line.get("account_id"):
            raise BusinessError("فيه سطر من غير حساب")
        out.append(PostingLine(int(line["account_id"]), dr, cr, (line.get("memo") or "").strip() or None))
    return out


def _balance_to_equity(session, lines: list[PostingLine]) -> list[PostingLine]:
    """Opening balances: put the difference on opening-balance equity so the entry balances."""
    diff = r2(sum(l.debit for l in lines) - sum(l.credit for l in lines))
    if diff == 0:
        return lines
    acc = _opening_equity(session)
    memo = "فرق الأرصدة الافتتاحية"
    extra = PostingLine(acc.id, 0.0, diff, memo) if diff > 0 else PostingLine(acc.id, -diff, 0.0, memo)
    return lines + [extra]


def save_journal(actor, journal_id: int | None, doc_date: date, memo: str, kind: str,
                 lines: list[dict], auto_balance: bool = False) -> int:
    """Create or edit a pending journal. Returns its id. Nothing posts until approval."""
    if kind not in JOURNAL_KINDS:
        raise BusinessError("نوع القيد غير معروف")
    if doc_date is None:
        raise BusinessError("التاريخ مطلوب")
    with session_scope() as s:
        if journal_id is None:
            check_access(actor, SCREEN, "add")
            doc = ManualJournal(created_by_id=actor.id)
        else:
            doc = s.get(ManualJournal, journal_id)
            if doc is None:
                raise BusinessError("القيد غير موجود")
            check_access(actor, SCREEN, "edit", doc)
            check_editable(doc)
            resubmit(doc)
        ensure_open(s, doc_date)
        posting = _clean_lines(lines)
        if kind == JOURNAL_OPENING and auto_balance:
            posting = _balance_to_equity(s, posting)
        validate_lines(s, posting)
        doc.doc_date, doc.memo, doc.kind = doc_date, (memo or "").strip() or None, kind
        doc.lines = [ManualJournalLine(account_id=l.account_id, debit=l.debit, credit=l.credit, memo=l.memo)
                     for l in posting]
        s.add(doc)
        s.flush()
        audit.log(s, actor, "create" if journal_id is None else "update", "manual_journal", doc.id,
                  {"doc_no": doc.doc_no, "total": r2(sum(l.debit for l in posting))})
        return doc.id


def delete_journal(actor, journal_id: int) -> None:
    with session_scope() as s:
        doc = s.get(ManualJournal, journal_id)
        if doc is None:
            return
        check_access(actor, SCREEN, "delete", doc)
        check_editable(doc)
        audit.log(s, actor, "delete", "manual_journal", doc.id, {"doc_no": doc.doc_no})
        s.delete(doc)


def get_journal(journal_id: int) -> dict:
    with session_scope() as s:
        doc = s.get(ManualJournal, journal_id)
        if doc is None:
            raise BusinessError("القيد غير موجود")
        return {
            "id": doc.id, "doc_no": doc.doc_no, "doc_date": doc.doc_date, "memo": doc.memo or "", "kind": doc.kind,
            "status": doc.status, "rejection_note": doc.rejection_note, "created_by_id": doc.created_by_id,
            "lines": [{"account_id": l.account_id, "debit": l.debit, "credit": l.credit, "memo": l.memo or ""}
                      for l in doc.lines],
        }


def journals_frame(actor, kind: str | None = None) -> pd.DataFrame:
    with session_scope() as s:
        names = dict(s.execute(select(User.id, User.full_name)).all())
        q = select(ManualJournal).order_by(ManualJournal.doc_date.desc(), ManualJournal.id.desc())
        if kind:
            q = q.where(ManualJournal.kind == kind)
        if own_only(actor, SCREEN):
            q = q.where(ManualJournal.created_by_id == actor.id)
        rows = [{
            "id": d.id, "رقم القيد": d.doc_no, "التاريخ": d.doc_date, "النوع": JOURNAL_KINDS[d.kind],
            "البيان": d.memo or "", "القيمة": r2(sum(l.debit for l in d.lines)), "الحالة": status_label(d.status),
            "ملاحظة المراجع": d.rejection_note or "", "سجّله": names.get(d.created_by_id, ""),
        } for d in s.scalars(q)]
    return pd.DataFrame(rows, columns=["id", "رقم القيد", "التاريخ", "النوع", "البيان", "القيمة", "الحالة",
                                       "ملاحظة المراجع", "سجّله"])


# ---------- Excel import of journal lines ----------

def lines_template() -> pd.DataFrame:
    return pd.DataFrame(columns=LINE_COLUMNS)


def validate_lines_import(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in LINE_COLUMNS[:3] if c not in df.columns]
    if missing:
        raise BusinessError("أعمدة ناقصة في الملف: " + "، ".join(missing))
    df = df.copy()
    if LINE_COLUMNS[3] not in df.columns:
        df[LINE_COLUMNS[3]] = ""
    df[LINE_COLUMNS[0]] = df[LINE_COLUMNS[0]].fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    for col in LINE_COLUMNS[1:3]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).round(2)
    df[LINE_COLUMNS[3]] = df[LINE_COLUMNS[3]].fillna("").astype(str)
    with session_scope() as s:
        accounts = {a.code: a for a in s.scalars(select(Account))}
        status = []
        for rec in df.to_dict("records"):
            acc = accounts.get(rec[LINE_COLUMNS[0]])
            dr, cr = rec[LINE_COLUMNS[1]], rec[LINE_COLUMNS[2]]
            if acc is None:
                status.append("الحساب غير موجود")
            elif not acc.is_postable:
                status.append("حساب رئيسي لا يقبل قيود")
            elif not acc.is_active:
                status.append("الحساب موقوف")
            elif dr < 0 or cr < 0:
                status.append("قيمة سالبة")
            elif (dr > 0) == (cr > 0):
                status.append("لازم مدين أو دائن (واحد بس)")
            else:
                status.append("جاهز")
    df["الحالة"] = status
    return df


def import_journal(actor, df: pd.DataFrame, doc_date: date, memo: str, kind: str = JOURNAL_REGULAR,
                   auto_balance: bool = False) -> int:
    """Create one pending journal from the file. Every row must be valid. Returns the number of lines."""
    checked = validate_lines_import(df)
    bad = checked[checked["الحالة"] != "جاهز"]
    if len(bad):
        raise BusinessError(f"فيه {len(bad)} سطر غير صالح. صححهم في الملف وارفعه تاني.")
    with session_scope() as s:
        ids = {a.code: a.id for a in s.scalars(select(Account))}
    lines = [{"account_id": ids[r[LINE_COLUMNS[0]]], "debit": r[LINE_COLUMNS[1]], "credit": r[LINE_COLUMNS[2]],
              "memo": r[LINE_COLUMNS[3]]} for r in checked.to_dict("records")]
    save_journal(actor, None, doc_date, memo, kind, lines, auto_balance)
    return len(lines)


# ---------- approval hooks ----------

def _describe(session, doc: ManualJournal):
    return JOURNAL_KINDS[doc.kind], doc.memo or "", r2(sum(l.debit for l in doc.lines))


def _build_lines(session, doc: ManualJournal) -> list[PostingLine]:
    return [PostingLine(l.account_id, l.debit, l.credit, l.memo) for l in doc.lines]


approval.register(approval.DocType("manual_journal", ManualJournal, SCREEN, _describe, _build_lines))
