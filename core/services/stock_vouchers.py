"""Stock vouchers: opening stock, gifts and samples in/out, issues, and stock-count adjustments."""
from datetime import date

import pandas as pd
from sqlalchemy import func, select

from core.db import session_scope
from core.models import Account, Item, ItemGroup, Party, StockVoucher, StockVoucherLine, User
from core.models.inventory import (COUNT_GAIN, COUNT_LOSS, GIFT_IN, GIFT_OUT, INBOUND_KINDS, ISSUE, STOCK_KINDS,
                                   STOCK_OPENING)
from core.services import approval, audit, stock
from core.services.approval import PostingLine, r2
from core.services.doc_rules import check_access, check_editable, own_only, resubmit, status_label
from core.services.errors import BusinessError
from core.services.journals import OPENING_SCREEN
from core.services.periods import ensure_open

SCREEN = "stock_vouchers"


def screen_for(kind: str) -> str:
    """Opening stock is entered under the opening-balances permission, like the opening journal."""
    return OPENING_SCREEN if kind == STOCK_OPENING else SCREEN
LINE_COLUMNS = ["كود الصنف", "اسم الصنف", "الكمية", "سعر الوحدة"]
MOVE_KIND_LABELS = STOCK_KINDS | {"purchase": "مشتريات", "purchase_return": "مرتجع مشتريات", "sale": "مبيعات",
                                  "sale_return": "مرتجع مبيعات", "production_issue": "صرف للإنتاج",
                                  "production_output": "استلام إنتاج"}
# Counter account per kind (the issue kind takes the user's choice).
COUNTER_KEYS = {STOCK_OPENING: "opening_balance_equity", GIFT_OUT: "gifts_expense", COUNT_GAIN: "stock_gain",
                COUNT_LOSS: "stock_loss"}


def save_voucher(actor, voucher_id: int | None, kind: str, doc_date: date, lines: list[dict],
                 party_id: int | None = None, counter_account_id: int | None = None, memo: str = "") -> int:
    """lines: dicts with item_id, qty, unit_cost (opening / count gain only), memo."""
    if kind not in STOCK_KINDS:
        raise BusinessError("نوع الإذن غير معروف")
    if doc_date is None:
        raise BusinessError("التاريخ مطلوب")
    with session_scope() as s:
        if kind == ISSUE:
            acc = s.get(Account, counter_account_id) if counter_account_id else None
            if acc is None or not acc.is_postable or not acc.is_active:
                raise BusinessError("اختار الحساب اللي هيتحمل الصرف (مصروف أو تكلفة)")
            if acc.code.startswith("13") or acc.system_key in approval.PARTY_ACCOUNTS:
                raise BusinessError("حساب الصرف لازم يكون مصروف أو تكلفة، مش مخزون أو عميل/مورد")
        else:
            counter_account_id = None
        if party_id and s.get(Party, party_id) is None:
            raise BusinessError("الجهة غير موجودة")
        clean = []
        for i, l in enumerate(lines, start=1):
            item_id, qty = l.get("item_id"), float(l.get("qty") or 0)
            cost = l.get("unit_cost")
            cost = None if cost is None or (isinstance(cost, float) and cost != cost) or cost == "" else float(cost)
            if not item_id and not qty:
                continue
            item = s.get(Item, item_id) if item_id else None
            if item is None:
                raise BusinessError(f"السطر {i}: اختار الصنف")
            if qty <= 0:
                raise BusinessError(f"السطر {i}: الكمية لازم تكون أكبر من صفر")
            group = s.get(ItemGroup, item.group_id)
            if kind == GIFT_IN and group.kind != "samples":
                raise BusinessError(f"السطر {i}: الهدايا الواردة بتدخل على أصناف مجموعة «عينات وهدايا واردة» "
                                    "عشان متأثرش على متوسط تكلفة الخامات")
            if kind == STOCK_OPENING and (cost is None or cost < 0):
                raise BusinessError(f"السطر {i}: سعر الوحدة مطلوب في الرصيد الافتتاحي")
            if cost is not None and cost < 0:
                raise BusinessError(f"السطر {i}: السعر مينفعش يكون سالب")
            keep_cost = kind in (STOCK_OPENING, COUNT_GAIN)
            clean.append(StockVoucherLine(item_id=item.id, qty=qty, unit_cost=cost if keep_cost else None,
                                          memo=(l.get("memo") or "").strip() or None))
        if not clean:
            raise BusinessError("الإذن لازم يكون فيه صنف واحد على الأقل")
        if voucher_id is None:
            check_access(actor, screen_for(kind), "add")
            doc = StockVoucher(created_by_id=actor.id)
        else:
            doc = s.get(StockVoucher, voucher_id)
            if doc is None:
                raise BusinessError("الإذن غير موجود")
            check_access(actor, screen_for(doc.kind), "edit", doc)
            if kind != doc.kind:
                check_access(actor, screen_for(kind), "add")
            check_editable(doc)
            resubmit(doc)
        ensure_open(s, doc_date)
        doc.kind, doc.doc_date, doc.party_id, doc.counter_account_id = kind, doc_date, party_id or None, counter_account_id
        doc.memo = (memo or "").strip() or None
        doc.lines = clean
        s.add(doc)
        s.flush()
        audit.log(s, actor, "create" if voucher_id is None else "update", "stock_voucher", doc.id, {"doc_no": doc.doc_no})
        return doc.id


def delete_voucher(actor, voucher_id: int) -> None:
    with session_scope() as s:
        doc = s.get(StockVoucher, voucher_id)
        if doc is None:
            return
        check_access(actor, screen_for(doc.kind), "delete", doc)
        check_editable(doc)
        audit.log(s, actor, "delete", "stock_voucher", doc.id, {"doc_no": doc.doc_no})
        s.delete(doc)


def get_voucher(voucher_id: int) -> dict:
    with session_scope() as s:
        d = s.get(StockVoucher, voucher_id)
        if d is None:
            raise BusinessError("الإذن غير موجود")
        return {"id": d.id, "doc_no": d.doc_no, "kind": d.kind, "doc_date": d.doc_date, "party_id": d.party_id,
                "counter_account_id": d.counter_account_id, "memo": d.memo or "", "status": d.status,
                "rejection_note": d.rejection_note, "created_by_id": d.created_by_id,
                "lines": [{"item_id": l.item_id, "qty": l.qty, "unit_cost": l.unit_cost, "memo": l.memo or ""}
                          for l in d.lines]}


def vouchers_frame(actor, kind: str | None = None) -> pd.DataFrame:
    """kind None lists every kind but opening stock, which lives on the opening-balances screen."""
    with session_scope() as s:
        names = dict(s.execute(select(User.id, User.full_name)).all())
        parties = {p.id: p.name for p in s.scalars(select(Party))}
        q = select(StockVoucher).order_by(StockVoucher.doc_date.desc(), StockVoucher.id.desc())
        q = q.where(StockVoucher.kind == kind) if kind else q.where(StockVoucher.kind != STOCK_OPENING)
        if own_only(actor, screen_for(kind)):
            q = q.where(StockVoucher.created_by_id == actor.id)
        rows = [{"id": d.id, "رقم الإذن": d.doc_no, "التاريخ": d.doc_date, "النوع": STOCK_KINDS[d.kind],
                 "الجهة": parties.get(d.party_id, ""), "عدد الأصناف": len(d.lines), "البيان": d.memo or "",
                 "الحالة": status_label(d.status), "ملاحظة المراجع": d.rejection_note or "",
                 "سجّله": names.get(d.created_by_id, "")} for d in s.scalars(q)]
    return pd.DataFrame(rows, columns=["id", "رقم الإذن", "التاريخ", "النوع", "الجهة", "عدد الأصناف", "البيان", "الحالة",
                                       "ملاحظة المراجع", "سجّله"])


# ---------- Excel import of lines (opening stock, counts) ----------

def lines_template() -> pd.DataFrame:
    return pd.DataFrame(columns=LINE_COLUMNS)


def validate_lines_import(df: pd.DataFrame) -> pd.DataFrame:
    if LINE_COLUMNS[2] not in df.columns or (LINE_COLUMNS[0] not in df.columns and LINE_COLUMNS[1] not in df.columns):
        raise BusinessError("الملف لازم يكون فيه الكمية وكود الصنف أو اسمه")
    df = df.copy()
    for col in LINE_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    for col in LINE_COLUMNS[:2]:
        df[col] = df[col].fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    df[LINE_COLUMNS[2]] = pd.to_numeric(df[LINE_COLUMNS[2]], errors="coerce")
    df[LINE_COLUMNS[3]] = pd.to_numeric(df[LINE_COLUMNS[3]], errors="coerce")
    with session_scope() as s:
        by_code = {i.code: i.id for i in s.scalars(select(Item))}
        by_name = {i.name.lower(): i.id for i in s.scalars(select(Item))}
    ids, status = [], []
    for rec in df.to_dict("records"):
        item_id = by_code.get(rec[LINE_COLUMNS[0]]) or by_name.get(rec[LINE_COLUMNS[1]].lower())
        qty, cost = rec[LINE_COLUMNS[2]], rec[LINE_COLUMNS[3]]
        ids.append(item_id)
        if item_id is None:
            status.append("الصنف غير موجود — سجّله الأول في شاشة الأصناف")
        elif not qty or qty != qty or qty <= 0:
            status.append("الكمية لازم تكون أكبر من صفر")
        elif cost == cost and cost is not None and cost < 0:
            status.append("السعر سالب")
        else:
            status.append("جاهز")
    df["item_id"] = ids
    df["الحالة"] = status
    return df


def import_voucher(actor, df: pd.DataFrame, kind: str, doc_date: date, memo: str = "") -> int:
    """One pending voucher from the whole file; every row must be valid. Returns the number of lines."""
    checked = validate_lines_import(df)
    bad = checked[checked["الحالة"] != "جاهز"]
    if len(bad):
        raise BusinessError(f"فيه {len(bad)} سطر غير صالح. صححهم في الملف وارفعه تاني.")
    lines = [{"item_id": int(r["item_id"]), "qty": float(r[LINE_COLUMNS[2]]),
              "unit_cost": None if r[LINE_COLUMNS[3]] != r[LINE_COLUMNS[3]] else float(r[LINE_COLUMNS[3]])}
             for r in checked.to_dict("records")]
    save_voucher(actor, None, kind, doc_date, lines, memo=memo)
    return len(lines)


# ---------- approval hooks ----------

def _plan(session, doc: StockVoucher) -> list[stock.PlannedMove]:
    if doc.kind in INBOUND_KINDS:
        moves = []
        for l in doc.lines:
            if doc.kind == GIFT_IN:
                cost = 0.0
            elif l.unit_cost is not None:
                cost = float(l.unit_cost)
            else:
                cost = stock.average_cost(session, l.item_id)
            moves.append(stock.PlannedMove(l.item_id, qty_in=l.qty, unit_cost=round(cost, 6), memo=l.memo))
        return moves
    return stock.plan_outbound(session, [(l.item_id, l.qty, l.memo) for l in doc.lines])


def _build_lines(session, doc: StockVoucher) -> list[PostingLine]:
    if doc.kind == GIFT_IN:
        return []
    moves = _plan(session, doc)
    if doc.kind == ISSUE:
        counter = doc.counter_account_id
    else:
        counter = session.scalar(select(Account.id).where(Account.system_key == COUNTER_KEYS[doc.kind]))
    memo = doc.memo or STOCK_KINDS[doc.kind]
    by_account: dict[int, float] = {}
    for m in moves:
        acc = stock.group_account(session, m.item_id)
        by_account[acc] = r2(by_account.get(acc, 0) + m.value)
    total = r2(sum(by_account.values()))
    inbound = doc.kind in INBOUND_KINDS
    lines = [PostingLine(acc, debit=v if inbound else 0, credit=0 if inbound else v, memo=memo)
             for acc, v in by_account.items()]
    lines.append(PostingLine(counter, debit=0 if inbound else total, credit=total if inbound else 0, memo=memo))
    return lines


def _on_post(session, doc: StockVoucher, entry_id) -> None:
    stock.write_moves(session, doc, "stock_voucher", doc.kind, _plan(session, doc), doc.party_id)


def _describe(session, doc: StockVoucher):
    party = session.get(Party, doc.party_id) if doc.party_id else None
    value = r2(sum(m.value for m in _plan_safe(session, doc)))
    text = " - ".join(x for x in (party.name if party else "", doc.memo, f"{len(doc.lines)} صنف") if x)
    return STOCK_KINDS[doc.kind], text, value


def _plan_safe(session, doc):
    try:
        return _plan(session, doc)
    except BusinessError:
        return []


approval.register(approval.DocType("stock_voucher", StockVoucher, SCREEN, _describe, _build_lines,
                                   on_post=_on_post, on_void=lambda s, d: stock.void_moves(s, "stock_voucher", d.id)))
