"""Bills of materials and material costing by method.

A component's price per unit comes from the method asked for, and an item with no
price of its own (an intermediate with no stock, for example) is priced from its own
recipe with the same method, down to the last level.
"""
import pandas as pd
from sqlalchemy import select

from core.db import session_scope
from core.models import Bom, BomLine, Item, ItemGroup, StockMove, User
from core.models.costing import LAST, MANUAL, WAC
from core.services import audit, stock
from core.services.errors import BusinessError

SCREEN = "boms"
KILO_UNITS = {"كيلو": 1000.0, "كجم": 1000.0, "كيلوجرام": 1000.0, "جرام": 1.0, "جم": 1.0}

SRC_STOCK = "رصيد المخزن"
SRC_LAST = "آخر شراء"
SRC_BOM = "من التركيبة"
SRC_LAST_COST = "آخر تكلفة مسجلة"
SRC_NONE = "مفيش سعر"


def grams_per_unit(unit: str) -> float | None:
    return KILO_UNITS.get((unit or "").strip())


def line_qty(component: Item, qty: float, in_grams: bool) -> float:
    """A BOM line's quantity in the component's own unit."""
    if not in_grams:
        return float(qty or 0)
    g = grams_per_unit(component.unit)
    if g is None:
        raise BusinessError(f"الصنف «{component.name}» وحدته {component.unit}؛ اكتب كميته بوحدته مش بالجرام")
    return float(qty or 0) / g


def _bom(session, item_id: int) -> Bom | None:
    return session.scalar(select(Bom).where(Bom.item_id == item_id))


def _reaches(session, start_id: int, target_id: int, seen: set | None = None) -> bool:
    """True when target is start itself or anywhere inside start's recipe tree."""
    if start_id == target_id:
        return True
    seen = seen if seen is not None else set()
    if start_id in seen:
        return False
    seen.add(start_id)
    bom = _bom(session, start_id)
    return bool(bom) and any(_reaches(session, l.component_id, target_id, seen) for l in bom.lines)


def save_bom(actor, item_id: int, lines: list[dict], output_qty: float = 1, waste_pct: float = 0,
             notes: str = "") -> int:
    """lines: dicts with component_id, qty, in_grams. Replaces the item's recipe."""
    if not (actor.can(SCREEN, "add") or actor.can(SCREEN, "edit")):
        raise BusinessError("ليس لديك صلاحية تعديل التركيبات")
    if float(output_qty or 0) <= 0:
        raise BusinessError("الكمية الناتجة من التركيبة لازم تكون أكبر من صفر")
    if not 0 <= float(waste_pct or 0) < 100:
        raise BusinessError("نسبة الهالك لازم تكون بين 0 و 100")
    clean = [l for l in lines if l.get("component_id") and float(l.get("qty") or 0) > 0]
    if not clean:
        raise BusinessError("التركيبة لازم يكون فيها خامة واحدة على الأقل بكمية")
    ids = [l["component_id"] for l in clean]
    if len(ids) != len(set(ids)):
        raise BusinessError("فيه خامة متكررة في التركيبة؛ اجمع كميتها في سطر واحد")
    with session_scope() as s:
        item = s.get(Item, item_id)
        if item is None:
            raise BusinessError("الصنف غير موجود")
        for l in clean:
            comp = s.get(Item, l["component_id"])
            if comp is None:
                raise BusinessError("خامة غير موجودة")
            line_qty(comp, l["qty"], bool(l.get("in_grams")))
            if _reaches(s, comp.id, item.id):
                raise BusinessError(f"«{comp.name}» داخل في تركيبة «{item.name}» أو العكس؛ مينفعش صنف يدخل في تركيبة نفسه")
        bom = _bom(s, item_id)
        created = bom is None
        if created:
            bom = Bom(item_id=item_id)
            s.add(bom)
        bom.output_qty, bom.waste_pct = float(output_qty), float(waste_pct or 0)
        bom.notes, bom.updated_by_id = (notes or "").strip() or None, actor.id
        bom.lines = [BomLine(component_id=l["component_id"], qty=float(l["qty"]), in_grams=bool(l.get("in_grams")))
                     for l in clean]
        s.flush()
        audit.log(s, actor, "create" if created else "update", "bom", bom.id,
                  {"item": item.code, "lines": len(clean), "waste_pct": bom.waste_pct})
        return bom.id


def delete_bom(actor, item_id: int) -> None:
    if not actor.can(SCREEN, "delete"):
        raise BusinessError("ليس لديك صلاحية الحذف")
    with session_scope() as s:
        bom = _bom(s, item_id)
        if bom is None:
            return
        audit.log(s, actor, "delete", "bom", bom.id, {"item_id": item_id})
        s.delete(bom)


def get_bom(item_id: int) -> dict | None:
    with session_scope() as s:
        bom = _bom(s, item_id)
        if bom is None:
            return None
        return {"id": bom.id, "item_id": item_id, "output_qty": float(bom.output_qty), "waste_pct": float(bom.waste_pct),
                "notes": bom.notes or "",
                "lines": [{"component_id": l.component_id, "qty": float(l.qty), "in_grams": l.in_grams} for l in bom.lines]}


def boms_frame() -> pd.DataFrame:
    with session_scope() as s:
        users = {u.id: u.full_name for u in s.scalars(select(User))}
        rows = []
        for bom in s.scalars(select(Bom).join(Item, Item.id == Bom.item_id).order_by(Item.code)):
            rows.append({"item_id": bom.item_id, "كود الصنف": bom.item.code, "الصنف": bom.item.name,
                         "الوحدة": bom.item.unit, "تكفي لـ (قطعة)": float(bom.output_qty), "عدد الخامات": len(bom.lines),
                         "الهالك %": float(bom.waste_pct), "وزن القطعة (جم)": piece_weight(s, bom.item_id),
                         "آخر تعديل": bom.updated_at.strftime("%Y-%m-%d %H:%M") if bom.updated_at else "",
                         "بواسطة": users.get(bom.updated_by_id, "")})
    return pd.DataFrame(rows, columns=["item_id", "كود الصنف", "الصنف", "الوحدة", "تكفي لـ (قطعة)", "عدد الخامات",
                                       "الهالك %", "وزن القطعة (جم)", "آخر تعديل", "بواسطة"])


def piece_weight(session, item_id: int) -> float | None:
    """Grams in one piece: the item card's weight, else the weighed materials in its recipe."""
    item = session.get(Item, item_id)
    if item is not None and item.piece_weight_g:
        return round(float(item.piece_weight_g), 3)
    bom = _bom(session, item_id)
    if bom is None:
        return None
    grams = 0.0
    for l in bom.lines:
        g = grams_per_unit(l.component.unit)
        if g is not None:
            grams += line_qty(l.component, l.qty, l.in_grams) * g
    return round(grams / float(bom.output_qty), 3) if grams else None


def per_piece_lines(session, item_id: int) -> list[tuple[Item, float]]:
    """(component, quantity per piece in its own unit) for an item's recipe."""
    bom = _bom(session, item_id)
    if bom is None:
        return []
    out = float(bom.output_qty)
    return [(l.component, line_qty(l.component, l.qty, l.in_grams) / out) for l in bom.lines]


def unit_price(session, item_id: int, method: str, _stack: tuple = ()) -> tuple[float, str]:
    """(price per unit of the item, where it came from) for WAC or last purchase price."""
    if item_id in _stack:
        return 0.0, SRC_NONE
    item = session.get(Item, item_id)
    if method == WAC:
        qty, value = stock.position(session, item_id)
        if qty > stock.EPS:
            return round(value / qty, 6), SRC_STOCK
    elif method == LAST:
        lp = stock.last_purchase_price(session, item_id)
        if lp is not None:
            return lp, SRC_LAST
    bom = _bom(session, item_id)
    if bom is not None and bom.lines:
        per_piece = sum(q * unit_price(session, c.id, method, _stack + (item_id,))[0]
                        for c, q in per_piece_lines(session, item_id))
        per_piece *= 1 + float(bom.waste_pct) / 100
        return round(per_piece * float(item.pieces_per_unit or 1), 6), SRC_BOM
    if method == WAC:
        has_moves = session.scalar(select(StockMove.id).where(StockMove.item_id == item_id).limit(1)) is not None
        if has_moves:
            return stock.average_cost(session, item_id), SRC_LAST_COST
    return 0.0, SRC_NONE


def costed_lines(item_id: int) -> list[dict]:
    """The recipe per piece with each material's WAC and last price, ready for a quote."""
    with session_scope() as s:
        rows = []
        for comp, q in per_piece_lines(s, item_id):
            wac, wac_src = unit_price(s, comp.id, WAC)
            last, last_src = unit_price(s, comp.id, LAST)
            rows.append({"component_id": comp.id, "name": comp.name, "unit": comp.unit, "qty": round(q, 6),
                         WAC: wac, LAST: last, MANUAL: last or wac, "wac_src": wac_src, "last_src": last_src})
        return rows


def recipe_items(group_kinds: tuple[str, ...] = ("finished", "compound")) -> dict[int, str]:
    """Items that can carry a recipe, with a mark on those that already have one."""
    with session_scope() as s:
        with_bom = set(s.scalars(select(Bom.item_id)))
        q = (select(Item).join(ItemGroup, ItemGroup.id == Item.group_id).where(ItemGroup.kind.in_(group_kinds),
                                                                              Item.is_active).order_by(Item.name))
        return {it.id: f"{'✓ ' if it.id in with_bom else ''}{it.name} ({it.unit}) - {it.code}" for it in s.scalars(q)}


def component_prices(item_ids: list[int]) -> dict[int, tuple[float, float]]:
    """{item id: (WAC price, last price)} per unit of each item."""
    with session_scope() as s:
        return {i: (unit_price(s, i, WAC)[0], unit_price(s, i, LAST)[0]) for i in set(item_ids) if i}


def item_defaults(item_id: int) -> dict:
    """What a new quote starts from: pieces per unit, piece weight, waste % and the costed recipe."""
    with session_scope() as s:
        item = s.get(Item, item_id)
        bom = _bom(s, item_id)
        out = {"pieces_per_unit": float(item.pieces_per_unit or 1), "piece_weight_g": piece_weight(s, item_id),
               "waste_pct": float(bom.waste_pct) if bom else 0.0, "has_bom": bom is not None, "unit": item.unit}
    out["lines"] = costed_lines(item_id)
    return out


def item_labels(item_ids: list[int]) -> dict[int, tuple[str, str]]:
    """{item id: (name, unit)}."""
    with session_scope() as s:
        return {it.id: (it.name, it.unit) for it in s.scalars(select(Item).where(Item.id.in_(set(item_ids))))}
