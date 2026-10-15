"""Stock ledger: weighted average cost, posting and voiding moves, and stock reports.

The average is perpetual: every inbound move (purchase, opening, gain) is blended into
the item's average, and every outbound move leaves at the average current at approval.
Stock may not go negative.
"""
from dataclasses import dataclass
from datetime import date

import pandas as pd
from sqlalchemy import func, select

from core.db import session_scope
from core.models import Account, Item, ItemGroup, JournalEntry, JournalLine, StockMove
from core.models.inventory import MOVE_POSTED, MOVE_VOID
from core.models.ledger import ENTRY_POSTED
from core.services.approval import r2
from core.services.errors import BusinessError

EPS = 1e-6


@dataclass
class PlannedMove:
    item_id: int
    qty_in: float = 0.0
    qty_out: float = 0.0
    unit_cost: float = 0.0
    memo: str | None = None

    @property
    def value(self) -> float:
        return r2((self.qty_in or self.qty_out) * self.unit_cost)


def position(session, item_id: int) -> tuple[float, float]:
    """(quantity, value) on hand from posted moves."""
    q = select(func.sum(StockMove.qty_in - StockMove.qty_out), func.sum(StockMove.value_in - StockMove.value_out)).where(
        StockMove.item_id == item_id, StockMove.status == MOVE_POSTED)
    qty, value = session.execute(q).one()
    return round(float(qty or 0), 4), r2(value)


def last_purchase_price(session, item_id: int) -> float | None:
    q = (select(StockMove.unit_cost).where(StockMove.item_id == item_id, StockMove.status == MOVE_POSTED,
                                           StockMove.kind == "purchase")
         .order_by(StockMove.move_date.desc(), StockMove.id.desc()).limit(1))
    v = session.scalar(q)
    return float(v) if v is not None else None


def average_cost(session, item_id: int) -> float:
    """Current WAC; falls back to the last posted unit cost when nothing is on hand."""
    qty, value = position(session, item_id)
    if qty > EPS:
        return round(value / qty, 6)
    q = (select(StockMove.unit_cost).where(StockMove.item_id == item_id, StockMove.status == MOVE_POSTED)
         .order_by(StockMove.move_date.desc(), StockMove.id.desc()).limit(1))
    v = session.scalar(q)
    return float(v or 0)


def plan_outbound(session, lines: list[tuple[int, float, str | None]]) -> list[PlannedMove]:
    """Cost (item, qty) issues at the current average; refuse more than is on hand.

    Several lines of the same item draw down one running position. The last unit of an item
    takes the remaining value exactly, so a fully issued item leaves no rounding residue.
    """
    running: dict[int, tuple[float, float]] = {}
    names = {}
    out = []
    for item_id, qty, memo in lines:
        if item_id not in running:
            running[item_id] = position(session, item_id)
            it = session.get(Item, item_id)
            names[item_id] = it.name if it else str(item_id)
        have_q, have_v = running[item_id]
        if qty > have_q + EPS:
            raise BusinessError(f"الرصيد غير كافي: {names[item_id]} — المتاح {have_q:,.4g} والمطلوب {qty:,.4g}")
        if abs(qty - have_q) <= EPS:
            value = have_v
        else:
            value = r2(qty * (have_v / have_q if have_q > EPS else average_cost(session, item_id)))
        unit = value / qty if qty else 0.0
        out.append(PlannedMove(item_id, qty_out=qty, unit_cost=round(unit, 6), memo=memo))
        running[item_id] = (round(have_q - qty, 4), r2(have_v - value))
    return out


def write_moves(session, doc, doc_type: str, kind: str, moves: list[PlannedMove], party_id: int | None = None) -> None:
    for m in moves:
        value = r2(m.qty_out * m.unit_cost) if m.qty_out else r2(m.qty_in * m.unit_cost)
        session.add(StockMove(
            item_id=m.item_id, move_date=doc.doc_date, qty_in=m.qty_in, qty_out=m.qty_out, unit_cost=m.unit_cost,
            value_in=value if m.qty_in else 0, value_out=value if m.qty_out else 0, source_type=doc_type,
            source_id=doc.id, source_no=doc.doc_no, kind=kind, party_id=party_id, memo=m.memo,
        ))
    session.flush()


def void_moves(session, doc_type: str, doc_id: int) -> None:
    """Void a document's moves; refuse if that would leave any item below zero."""
    moves = list(session.scalars(select(StockMove).where(StockMove.source_type == doc_type,
                                                         StockMove.source_id == doc_id,
                                                         StockMove.status == MOVE_POSTED)))
    for m in moves:
        m.status = MOVE_VOID
    session.flush()
    for item_id in {m.item_id for m in moves}:
        qty, _ = position(session, item_id)
        if qty < -EPS:
            it = session.get(Item, item_id)
            raise BusinessError(f"مينفعش تلغي الاعتماد: رصيد {it.name} هيبقى بالسالب لأنه اتصرف بعد كده")


def group_account(session, item_id: int) -> int:
    it = session.get(Item, item_id)
    if it is None:
        raise BusinessError("الصنف غير موجود")
    return session.get(ItemGroup, it.group_id).inventory_account_id


# ---------------------------------------------------------------- reports

def balances_frame(as_of: date | None = None, group_id: int | None = None, hide_zero: bool = True) -> pd.DataFrame:
    with session_scope() as s:
        q = (select(StockMove.item_id, func.sum(StockMove.qty_in - StockMove.qty_out),
                    func.sum(StockMove.value_in - StockMove.value_out))
             .where(StockMove.status == MOVE_POSTED).group_by(StockMove.item_id))
        if as_of:
            q = q.where(StockMove.move_date <= as_of)
        pos = {i: (round(float(qq or 0), 4), r2(v)) for i, qq, v in s.execute(q)}
        iq = select(Item, ItemGroup).join(ItemGroup, ItemGroup.id == Item.group_id).order_by(ItemGroup.id, Item.name)
        if group_id:
            iq = iq.where(Item.group_id == group_id)
        rows = []
        for it, g in s.execute(iq):
            qty, value = pos.get(it.id, (0.0, 0.0))
            if hide_zero and abs(qty) <= EPS and abs(value) < 0.005:
                continue
            avg = round(value / qty, 4) if qty > EPS else 0.0
            lpp = last_purchase_price(s, it.id)
            rows.append({"item_id": it.id, "الكود": it.code, "الصنف": it.name, "المجموعة": g.name, "الوحدة": it.unit,
                         "الكمية": qty, "متوسط التكلفة": avg, "القيمة": value,
                         "آخر سعر شراء": lpp if lpp is not None else float("nan"), "حد الطلب": it.min_qty,
                         "تحت حد الطلب": bool(it.min_qty and qty < it.min_qty)})
    return pd.DataFrame(rows, columns=["item_id", "الكود", "الصنف", "المجموعة", "الوحدة", "الكمية", "متوسط التكلفة",
                                       "القيمة", "آخر سعر شراء", "حد الطلب", "تحت حد الطلب"])


def item_card(item_id: int, date_from: date, date_to: date) -> tuple[pd.DataFrame, dict]:
    from core.models import Party
    from core.services.stock_vouchers import MOVE_KIND_LABELS

    with session_scope() as s:
        it = s.get(Item, item_id)
        if it is None:
            raise BusinessError("الصنف غير موجود")
        parties = {p.id: p.name for p in s.scalars(select(Party))}
        base = select(StockMove).where(StockMove.item_id == item_id, StockMove.status == MOVE_POSTED)
        oq = oval = 0.0
        for m in s.scalars(base.where(StockMove.move_date < date_from)):
            oq += m.qty_in - m.qty_out
            oval += m.value_in - m.value_out
        qty, value = round(oq, 4), r2(oval)
        rows = [{"التاريخ": date_from, "المستند": "", "الحركة": "رصيد أول المدة", "الجهة": "", "وارد": 0.0, "منصرف": 0.0,
                 "سعر الوحدة": 0.0, "قيمة الوارد": 0.0, "قيمة المنصرف": 0.0, "الرصيد": qty, "قيمة الرصيد": value,
                 "المتوسط": round(value / qty, 4) if qty > EPS else 0.0}]
        q = base.where(StockMove.move_date >= date_from, StockMove.move_date <= date_to).order_by(
            StockMove.move_date, StockMove.id)
        for m in s.scalars(q):
            qty = round(qty + m.qty_in - m.qty_out, 4)
            value = r2(value + m.value_in - m.value_out)
            rows.append({"التاريخ": m.move_date, "المستند": m.source_no or "", "الحركة": MOVE_KIND_LABELS.get(m.kind, m.kind),
                         "الجهة": parties.get(m.party_id, ""), "وارد": m.qty_in, "منصرف": m.qty_out,
                         "سعر الوحدة": m.unit_cost, "قيمة الوارد": m.value_in, "قيمة المنصرف": m.value_out,
                         "الرصيد": qty, "قيمة الرصيد": value, "المتوسط": round(value / qty, 4) if qty > EPS else 0.0})
        info = {"name": it.name, "code": it.code, "unit": it.unit, "qty": qty, "value": value}
    return pd.DataFrame(rows), info


def reconciliation(as_of: date) -> pd.DataFrame:
    """Stock value per inventory account vs. that account's GL balance."""
    with session_scope() as s:
        q = (select(ItemGroup.inventory_account_id, func.sum(StockMove.value_in - StockMove.value_out))
             .join(Item, Item.id == StockMove.item_id).join(ItemGroup, ItemGroup.id == Item.group_id)
             .where(StockMove.status == MOVE_POSTED, StockMove.move_date <= as_of)
             .group_by(ItemGroup.inventory_account_id))
        stock = {a: r2(v) for a, v in s.execute(q)}
        gq = (select(JournalLine.account_id, func.sum(JournalLine.debit - JournalLine.credit))
              .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
              .where(JournalEntry.status == ENTRY_POSTED, JournalEntry.entry_date <= as_of)
              .group_by(JournalLine.account_id))
        gl = {a: r2(v) for a, v in s.execute(gq)}
        rows = []
        linked = set(s.scalars(select(ItemGroup.inventory_account_id)))
        for acc in s.scalars(select(Account).where(Account.id.in_(linked)).order_by(Account.code)):
            sv, gv = stock.get(acc.id, 0.0), gl.get(acc.id, 0.0)
            if sv or gv:
                rows.append({"الحساب": f"{acc.code} - {acc.name_ar}", "قيمة المخزون": sv, "رصيد الحساب": gv,
                             "الفرق": r2(gv - sv)})
    return pd.DataFrame(rows, columns=["الحساب", "قيمة المخزون", "رصيد الحساب", "الفرق"])
