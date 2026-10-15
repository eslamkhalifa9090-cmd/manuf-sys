"""Item groups (each tied to an inventory account) and items."""
import pandas as pd
from sqlalchemy import func, select

from core.db import session_scope
from core.models import Account, Item, ItemGroup, StockMove
from core.models.inventory import GROUP_KINDS
from core.services import audit
from core.services.errors import BusinessError

SCREEN = "items"
UNITS = ["عدد", "كيلو", "جرام", "متر", "لتر", "جوز", "كيس", "علبة", "شيكارة", "طقم", "لفة"]
IMPORT_COLUMNS = ["كود الصنف", "اسم الصنف", "المجموعة", "الوحدة", "البند / العائلة", "حد الطلب"]

# Default groups, keyed by kind, with the inventory account each posts to.
DEFAULT_GROUPS = [
    ("خامات", "raw", "inv_raw"),
    ("تضريبة وخامات وسيطة", "compound", "inv_intermediate"),
    ("إنتاج تام", "finished", "inv_finished"),
    ("قطع غيار ومهمات", "spares", "inv_spares"),
    ("مستلزمات تعبئة", "packaging", "inv_packaging"),
    ("اكسسوارات", "accessories", "inv_accessories"),
    ("عينات وهدايا واردة", "samples", "inv_samples"),
]


def seed_groups(session) -> int:
    added = 0
    have = set(session.scalars(select(ItemGroup.name)))
    for name, kind, key in DEFAULT_GROUPS:
        if name in have:
            continue
        acc_id = session.scalar(select(Account.id).where(Account.system_key == key))
        if acc_id:
            session.add(ItemGroup(name=name, kind=kind, inventory_account_id=acc_id))
            added += 1
    session.flush()
    return added


def group_options(include_inactive: bool = False) -> dict[int, str]:
    with session_scope() as s:
        q = select(ItemGroup).order_by(ItemGroup.id)
        if not include_inactive:
            q = q.where(ItemGroup.is_active)
        return {g.id: g.name for g in s.scalars(q)}


def groups_frame() -> pd.DataFrame:
    with session_scope() as s:
        accounts = {a.id: f"{a.code} - {a.name_ar}" for a in s.scalars(select(Account))}
        counts = dict(s.execute(select(Item.group_id, func.count(Item.id)).group_by(Item.group_id)).all())
        rows = [{"id": g.id, "المجموعة": g.name, "النوع": GROUP_KINDS.get(g.kind, g.kind),
                 "حساب المخزون": accounts.get(g.inventory_account_id, ""), "عدد الأصناف": counts.get(g.id, 0),
                 "نشطة": g.is_active} for g in s.scalars(select(ItemGroup).order_by(ItemGroup.id))]
    return pd.DataFrame(rows)


def inventory_accounts() -> dict[int, str]:
    """Postable accounts under the inventory group (13)."""
    with session_scope() as s:
        q = select(Account).where(Account.code.startswith("13"), Account.is_postable, Account.is_active).order_by(Account.code)
        return {a.id: f"{a.code} - {a.name_ar}" for a in s.scalars(q)}


def save_group(actor, group_id: int | None, name: str, kind: str, inventory_account_id: int, is_active: bool = True) -> int:
    if not actor.can(SCREEN, "edit"):
        raise BusinessError("ليس لديك صلاحية تعديل المجموعات")
    name = (name or "").strip()
    if not name:
        raise BusinessError("اسم المجموعة مطلوب")
    if kind not in GROUP_KINDS:
        raise BusinessError("نوع المجموعة غير معروف")
    if inventory_account_id not in inventory_accounts():
        raise BusinessError("اختار حساب مخزون فرعي تحت المجموعة 13")
    with session_scope() as s:
        dup = s.scalar(select(ItemGroup.id).where(ItemGroup.name == name))
        if dup and dup != group_id:
            raise BusinessError("اسم المجموعة موجود")
        g = s.get(ItemGroup, group_id) if group_id else ItemGroup()
        if group_id and g is None:
            raise BusinessError("المجموعة غير موجودة")
        if group_id and g.inventory_account_id != inventory_account_id and _group_has_moves(s, g.id):
            raise BusinessError("المجموعة عليها حركات مخزنية؛ مينفعش تغيّر حساب المخزون بتاعها")
        g.name, g.kind, g.inventory_account_id, g.is_active = name, kind, inventory_account_id, is_active
        s.add(g)
        s.flush()
        audit.log(s, actor, "create" if group_id is None else "update", "item_group", g.id, {"name": name})
        return g.id


def _group_has_moves(session, group_id: int) -> bool:
    q = select(StockMove.id).join(Item, Item.id == StockMove.item_id).where(Item.group_id == group_id).limit(1)
    return session.scalar(q) is not None


def _next_code(session) -> str:
    n = session.scalar(select(func.count(Item.id))) or 0
    while True:
        n += 1
        code = f"I-{n:05d}"
        if session.scalar(select(Item.id).where(Item.code == code)) is None:
            return code


def save_item(actor, item_id: int | None, name: str, group_id: int, unit: str, code: str = "", family: str = "",
              min_qty: float = 0, notes: str = "", is_active: bool = True, pieces_per_unit: float = 1,
              piece_weight_g: float | None = None) -> int:
    name, code = (name or "").strip(), (code or "").strip()
    if not name:
        raise BusinessError("اسم الصنف مطلوب")
    if not (unit or "").strip():
        raise BusinessError("الوحدة مطلوبة")
    if float(pieces_per_unit or 0) <= 0:
        raise BusinessError("عدد القطع في الوحدة لازم يكون أكبر من صفر")
    if piece_weight_g is not None and float(piece_weight_g) < 0:
        raise BusinessError("وزن القطعة مينفعش يكون بالسالب")
    with session_scope() as s:
        if s.get(ItemGroup, group_id) is None:
            raise BusinessError("اختار المجموعة")
        dup = s.scalar(select(Item.id).where(func.lower(Item.name) == name.lower()))
        if dup and dup != item_id:
            raise BusinessError(f"الصنف «{name}» موجود")
        if code:
            dup = s.scalar(select(Item.id).where(Item.code == code))
            if dup and dup != item_id:
                raise BusinessError(f"الكود {code} مستخدم")
        if item_id is None:
            if not actor.can(SCREEN, "add"):
                raise BusinessError("ليس لديك صلاحية الإضافة")
            it = Item(code=code or _next_code(s))
            s.add(it)
        else:
            if not actor.can(SCREEN, "edit"):
                raise BusinessError("ليس لديك صلاحية التعديل")
            it = s.get(Item, item_id)
            if it is None:
                raise BusinessError("الصنف غير موجود")
            has_moves = s.scalar(select(StockMove.id).where(StockMove.item_id == it.id).limit(1)) is not None
            if has_moves and it.group_id != group_id:
                old, new = s.get(ItemGroup, it.group_id), s.get(ItemGroup, group_id)
                if old.inventory_account_id != new.inventory_account_id:
                    raise BusinessError("الصنف عليه حركات؛ مينفعش تنقله لمجموعة على حساب مخزون مختلف")
            if code:
                it.code = code
        it.name, it.group_id, it.unit = name, group_id, unit.strip()
        it.family, it.min_qty = (family or "").strip() or None, float(min_qty or 0)
        it.notes, it.is_active = (notes or "").strip() or None, is_active
        it.pieces_per_unit, it.piece_weight_g = float(pieces_per_unit), float(piece_weight_g or 0) or None
        s.flush()
        audit.log(s, actor, "create" if item_id is None else "update", "item", it.id, {"code": it.code, "name": name})
        return it.id


def delete_item(actor, item_id: int) -> None:
    if not actor.can(SCREEN, "delete"):
        raise BusinessError("ليس لديك صلاحية الحذف")
    from core.models import Bom, BomLine, PriceQuote, PriceQuoteLine, PurchaseLine, StockVoucherLine

    with session_scope() as s:
        it = s.get(Item, item_id)
        if it is None:
            return
        for q in (select(StockMove.id).where(StockMove.item_id == item_id),
                  select(PurchaseLine.id).where(PurchaseLine.item_id == item_id),
                  select(StockVoucherLine.id).where(StockVoucherLine.item_id == item_id)):
            if s.scalar(q.limit(1)) is not None:
                raise BusinessError("الصنف عليه حركات أو مستندات؛ أوقفه بدلاً من الحذف")
        for q in (select(BomLine.id).where(BomLine.component_id == item_id),
                  select(PriceQuote.id).where(PriceQuote.item_id == item_id),
                  select(PriceQuoteLine.id).where(PriceQuoteLine.component_id == item_id)):
            if s.scalar(q.limit(1)) is not None:
                raise BusinessError("الصنف داخل في تركيبة أو عرض سعر؛ أوقفه بدلاً من الحذف")
        bom = s.scalar(select(Bom).where(Bom.item_id == item_id))
        if bom is not None:
            s.delete(bom)
        audit.log(s, actor, "delete", "item", it.id, {"code": it.code, "name": it.name})
        s.delete(it)


def item_options(group_kinds: tuple[str, ...] | None = None, include_inactive: bool = False) -> dict[int, str]:
    with session_scope() as s:
        q = select(Item, ItemGroup).join(ItemGroup, ItemGroup.id == Item.group_id).order_by(Item.name)
        if group_kinds:
            q = q.where(ItemGroup.kind.in_(group_kinds))
        if not include_inactive:
            q = q.where(Item.is_active)
        return {it.id: f"{it.name} ({it.unit}) - {it.code}" for it, _ in s.execute(q)}


def items_frame() -> pd.DataFrame:
    with session_scope() as s:
        q = select(Item, ItemGroup).join(ItemGroup, ItemGroup.id == Item.group_id).order_by(Item.code)
        rows = [{"id": it.id, "الكود": it.code, "اسم الصنف": it.name, "المجموعة": g.name, "الوحدة": it.unit,
                 "البند / العائلة": it.family or "", "حد الطلب": it.min_qty,
                 "قطع في الوحدة": float(it.pieces_per_unit or 1), "وزن القطعة (جم)": it.piece_weight_g,
                 "نشط": it.is_active, "ملاحظات": it.notes or ""}
                for it, g in s.execute(q)]
    return pd.DataFrame(rows, columns=["id", "الكود", "اسم الصنف", "المجموعة", "الوحدة", "البند / العائلة", "حد الطلب",
                                       "قطع في الوحدة", "وزن القطعة (جم)", "نشط", "ملاحظات"])


# ---------- Excel import ----------

def import_template() -> pd.DataFrame:
    return pd.DataFrame(columns=IMPORT_COLUMNS)


def validate_import(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in (IMPORT_COLUMNS[1], IMPORT_COLUMNS[2]) if c not in df.columns]
    if missing:
        raise BusinessError("أعمدة ناقصة في الملف: " + "، ".join(missing))
    df = df.copy()
    for col in IMPORT_COLUMNS:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    with session_scope() as s:
        names = {n.lower() for n in s.scalars(select(Item.name))}
        codes = set(s.scalars(select(Item.code)))
        groups = {g.name for g in s.scalars(select(ItemGroup).where(ItemGroup.is_active))}
    seen_n, seen_c, status = set(), set(), []
    for rec in df.to_dict("records"):
        code, name, group = rec[IMPORT_COLUMNS[0]], rec[IMPORT_COLUMNS[1]], rec[IMPORT_COLUMNS[2]]
        min_qty = rec[IMPORT_COLUMNS[5]]
        if not name:
            status.append("اسم الصنف مطلوب")
        elif name.lower() in names or name.lower() in seen_n:
            status.append("الصنف موجود")
        elif code and (code in codes or code in seen_c):
            status.append("الكود مستخدم")
        elif group not in groups:
            status.append("المجموعة غير موجودة")
        elif min_qty and pd.to_numeric(min_qty, errors="coerce") != pd.to_numeric(min_qty, errors="coerce"):
            status.append("حد الطلب لازم يكون رقم")
        else:
            status.append("جاهز")
            seen_n.add(name.lower())
            if code:
                seen_c.add(code)
    df["الحالة"] = status
    return df


def import_items(actor, df: pd.DataFrame) -> int:
    checked = validate_import(df)
    with session_scope() as s:
        groups = {g.name: g.id for g in s.scalars(select(ItemGroup))}
    count = 0
    for rec in checked[checked["الحالة"] == "جاهز"].to_dict("records"):
        save_item(actor, None, rec[IMPORT_COLUMNS[1]], groups[rec[IMPORT_COLUMNS[2]]], rec[IMPORT_COLUMNS[3]] or "عدد",
                  rec[IMPORT_COLUMNS[0]], rec[IMPORT_COLUMNS[4]], float(pd.to_numeric(rec[IMPORT_COLUMNS[5]] or 0)))
        count += 1
    return count
