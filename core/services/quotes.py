"""Price quotes: the three-way costing, saved as frozen, revisioned snapshots.

Everything a quote needs to recalculate is stored on it (material prices, rates, mold
and overhead settings), so its figures never move after saving. Changing a saved quote
creates the next revision and leaves the old one read-only. Quotes post nothing.
"""
from datetime import date

import pandas as pd
from sqlalchemy import func, select

from core.db import session_scope
from core.models import Item, Party, PriceQuote, PriceQuoteLine, User
from core.models.costing import (BASES, ENTRY_BASES, ENTRY_PIECE, LAST, MANUAL, METHODS, MOLD_LIFE, MOLD_MODES, MOLD_NONE, MOLD_ORDER, PCT,
                                 Q_ACCEPTED, Q_DRAFT, QUOTE_STATUSES, WAC)
from core.services import audit
from core.services.errors import BusinessError

SCREEN = "pricing"
CONV_FIELDS = ("wages", "energy", "maintenance", "other")
COPY_FIELDS = ("quote_date", "party_id", "item_id", "qty", "pieces_per_unit", "piece_weight_g", "method", "entry_basis",
               "waste_pct",
               *CONV_FIELDS, "mold_mode", "mold_name", "mold_cost", "mold_life_pieces", "ga_basis", "ga_value",
               "selling_basis", "selling_value", "margin_pct", "offered_price", "notes")
PRICE_FIELD = {WAC: "wac_price", LAST: "last_price", MANUAL: "manual_price"}


def _f(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v != v else v


def _overhead(basis: str, value: float, base: float) -> float:
    return base * value / 100 if basis == PCT else value


def calc(q: dict) -> dict[str, dict]:
    """Cost build-up per piece for each method. `q` holds the quote fields and its lines."""
    ppu = _f(q.get("pieces_per_unit")) or 1
    pieces = _f(q.get("qty")) * ppu
    weight = _f(q.get("piece_weight_g"))
    conversion = sum(_f(q.get(f)) for f in CONV_FIELDS)
    mold_cost, mode = _f(q.get("mold_cost")), q.get("mold_mode") or MOLD_NONE
    if mode == MOLD_ORDER:
        mold = mold_cost / pieces if pieces else 0.0
    elif mode == MOLD_LIFE:
        life = _f(q.get("mold_life_pieces"))
        mold = mold_cost / life if life else 0.0
    else:
        mold = 0.0
    margin = _f(q.get("margin_pct"))
    out = {}
    for m, field in PRICE_FIELD.items():
        materials = sum(_f(l.get("qty")) * _f(l.get(field)) for l in q.get("lines", []))
        direct = materials + conversion
        waste = direct * _f(q.get("waste_pct")) / 100
        production = direct + waste + mold
        ga = _overhead(q.get("ga_basis") or PCT, _f(q.get("ga_value")), production)
        selling = _overhead(q.get("selling_basis") or PCT, _f(q.get("selling_value")), production)
        total = production + ga + selling
        price = total / (1 - margin / 100) if margin < 100 else 0.0
        out[m] = {
            "materials": materials, "conversion": conversion, "waste": waste, "mold": mold, "production": production,
            "ga": ga, "selling": selling, "total": total, "profit": price - total, "price_piece": price,
            "markup_pct": (price / total - 1) * 100 if total else 0.0,
            "price_unit": price * ppu, "price_kg": price / (weight / 1000) if weight else None,
            "order_value": price * pieces, "pieces": pieces,
        }
    offered = _f(q.get("offered_price"))
    if offered:
        piece_price = offered / ppu
        for r in out.values():
            r["offered_margin_pct"] = (piece_price - r["total"]) / piece_price * 100 if piece_price else 0.0
    return out


def _validate(data: dict) -> None:
    if not data.get("item_id"):
        raise BusinessError("اختار المنتج")
    if data.get("quote_date") is None:
        raise BusinessError("التاريخ مطلوب")
    if _f(data.get("qty")) <= 0:
        raise BusinessError("الكمية المطلوبة لازم تكون أكبر من صفر")
    if _f(data.get("pieces_per_unit")) <= 0:
        raise BusinessError("عدد القطع في الوحدة لازم يكون أكبر من صفر")
    if not [l for l in data.get("lines", []) if l.get("component_id") and _f(l.get("qty")) > 0]:
        raise BusinessError("العرض لازم يكون فيه خامة واحدة على الأقل")
    if not 0 <= _f(data.get("margin_pct")) < 100:
        raise BusinessError("هامش الربح لازم يكون بين 0 و أقل من 100%")
    if not 0 <= _f(data.get("waste_pct")) < 100:
        raise BusinessError("نسبة الهالك لازم تكون بين 0 و 100")
    if (data.get("entry_basis") or ENTRY_PIECE) not in ENTRY_BASES:
        raise BusinessError("طريقة الإدخال غير معروفة")
    if data.get("method") not in METHODS:
        raise BusinessError("اختار طريقة التسعير")
    if data.get("mold_mode") not in MOLD_MODES:
        raise BusinessError("اختار طريقة تحميل الأسطمبة")
    if data["mold_mode"] != MOLD_NONE and _f(data.get("mold_cost")) <= 0:
        raise BusinessError("اكتب تكلفة الأسطمبة أو اختار «بدون تحميل»")
    if data["mold_mode"] == MOLD_LIFE and _f(data.get("mold_life_pieces")) <= 0:
        raise BusinessError("اكتب العمر الافتراضي للأسطمبة بالقطع")
    for basis in (data.get("ga_basis"), data.get("selling_basis")):
        if basis not in BASES:
            raise BusinessError("أساس تحميل المصروفات غير معروف")
    for f in (*CONV_FIELDS, "mold_cost", "ga_value", "selling_value"):
        if _f(data.get(f)) < 0:
            raise BusinessError("مينفعش قيم بالسالب في التكاليف")


def _fill(q: PriceQuote, data: dict) -> None:
    for f in COPY_FIELDS:
        v = data.get(f)
        if f in ("mold_name", "notes"):
            v = (v or "").strip() or None
        elif f in ("piece_weight_g", "offered_price"):
            v = _f(v) or None
        elif f == "entry_basis":
            v = v or ENTRY_PIECE
        elif f not in ("party_id", "item_id", "quote_date", "method", "mold_mode", "ga_basis", "selling_basis"):
            v = _f(v)
        setattr(q, f, v)
    q.lines = [PriceQuoteLine(component_id=l["component_id"], qty=_f(l["qty"]), in_grams=bool(l.get("in_grams")),
                              wac_price=_f(l.get("wac_price")),
                              last_price=_f(l.get("last_price")), manual_price=_f(l.get("manual_price")))
               for l in data["lines"] if l.get("component_id") and _f(l.get("qty")) > 0]


def save_quote(actor, quote_id: int | None, data: dict) -> int:
    """New quote, or the next revision of an existing one. Returns the saved row's id."""
    _validate(data)
    with session_scope() as s:
        if s.get(Item, data["item_id"]) is None:
            raise BusinessError("المنتج غير موجود")
        if data.get("party_id") and s.get(Party, data["party_id"]) is None:
            raise BusinessError("العميل غير موجود")
        if quote_id is None:
            if not actor.can(SCREEN, "add"):
                raise BusinessError("ليس لديك صلاحية الإضافة")
            q = PriceQuote(quote_no=(s.scalar(select(func.max(PriceQuote.quote_no))) or 0) + 1, revision=1)
        else:
            if not actor.can(SCREEN, "edit"):
                raise BusinessError("ليس لديك صلاحية التعديل")
            old = s.get(PriceQuote, quote_id)
            if old is None:
                raise BusinessError("العرض غير موجود")
            if not old.is_current:
                raise BusinessError("ده إصدار قديم؛ التعديل بيكون على آخر إصدار بس")
            if old.status == Q_ACCEPTED:
                raise BusinessError("العرض اتقبل؛ مينفعش يتعدل. اعمل عرض جديد")
            old.is_current = False
            q = PriceQuote(quote_no=old.quote_no, revision=old.revision + 1)
        q.status, q.is_current, q.created_by_id = Q_DRAFT, True, actor.id
        _fill(q, data)
        s.add(q)
        s.flush()
        audit.log(s, actor, "create" if quote_id is None else "revise", "price_quote", q.id,
                  {"no": q.doc_no, "item_id": q.item_id, "method": q.method})
        return q.id


def set_status(actor, quote_id: int, status: str) -> None:
    if not actor.can(SCREEN, "edit"):
        raise BusinessError("ليس لديك صلاحية التعديل")
    if status not in QUOTE_STATUSES:
        raise BusinessError("حالة غير معروفة")
    with session_scope() as s:
        q = s.get(PriceQuote, quote_id)
        if q is None or not q.is_current:
            raise BusinessError("الحالة بتتغير على آخر إصدار بس")
        q.status = status
        audit.log(s, actor, "status", "price_quote", q.id, {"no": q.doc_no, "status": status})


def delete_quote(actor, quote_id: int) -> None:
    """Delete the latest revision while it is a draft; the one before it becomes current again."""
    if not actor.can(SCREEN, "delete"):
        raise BusinessError("ليس لديك صلاحية الحذف")
    with session_scope() as s:
        q = s.get(PriceQuote, quote_id)
        if q is None:
            return
        if not q.is_current or q.status != Q_DRAFT:
            raise BusinessError("بيتحذف آخر إصدار بس وهو مسودة")
        prev = s.scalar(select(PriceQuote).where(PriceQuote.quote_no == q.quote_no,
                                                 PriceQuote.revision == q.revision - 1))
        audit.log(s, actor, "delete", "price_quote", q.id, {"no": q.doc_no})
        s.delete(q)
        s.flush()
        if prev is not None:
            prev.is_current = True


def _as_dict(q: PriceQuote, s) -> dict:
    d = {f: getattr(q, f) for f in COPY_FIELDS}
    for f in (*CONV_FIELDS, "qty", "pieces_per_unit", "waste_pct", "mold_cost", "mold_life_pieces", "ga_value",
              "selling_value", "margin_pct"):
        d[f] = _f(d[f])
    party = s.get(Party, q.party_id) if q.party_id else None
    d.update({"id": q.id, "doc_no": q.doc_no, "quote_no": q.quote_no, "revision": q.revision, "status": q.status,
              "is_current": q.is_current, "item_name": q.item.name, "item_code": q.item.code, "unit": q.item.unit,
              "party_name": party.name if party else "", "created_at": q.created_at,
              "lines": [{"component_id": l.component_id, "name": l.component.name, "unit": l.component.unit,
                         "qty": _f(l.qty), "in_grams": bool(l.in_grams), "wac_price": _f(l.wac_price),
                         "last_price": _f(l.last_price),
                         "manual_price": _f(l.manual_price)} for l in q.lines]})
    return d


def get_quote(quote_id: int) -> dict:
    with session_scope() as s:
        q = s.get(PriceQuote, quote_id)
        if q is None:
            raise BusinessError("العرض غير موجود")
        return _as_dict(q, s)


def revisions(quote_no: int) -> list[dict]:
    with session_scope() as s:
        users = {u.id: u.full_name for u in s.scalars(select(User))}
        return [{"id": q.id, "doc_no": q.doc_no, "revision": q.revision, "status": q.status, "is_current": q.is_current,
                 "created_at": q.created_at, "by": users.get(q.created_by_id, "")}
                for q in s.scalars(select(PriceQuote).where(PriceQuote.quote_no == quote_no)
                                   .order_by(PriceQuote.revision.desc()))]


def quotes_frame(current_only: bool = True, status: str | None = None, party_id: int | None = None) -> pd.DataFrame:
    with session_scope() as s:
        users = {u.id: u.full_name for u in s.scalars(select(User))}
        parties = {p.id: p.name for p in s.scalars(select(Party))}
        q = select(PriceQuote).order_by(PriceQuote.quote_no.desc(), PriceQuote.revision.desc())
        if current_only:
            q = q.where(PriceQuote.is_current)
        if status:
            q = q.where(PriceQuote.status == status)
        if party_id:
            q = q.where(PriceQuote.party_id == party_id)
        rows = []
        for pq in s.scalars(q):
            d = _as_dict(pq, s)
            r = calc(d)[pq.method]
            offered = d["offered_price"] or r["price_unit"]
            rows.append({"id": pq.id, "رقم العرض": pq.doc_no, "التاريخ": pq.quote_date, "العميل": parties.get(pq.party_id, ""),
                         "المنتج": pq.item.name, "الكمية": d["qty"], "الوحدة": pq.item.unit,
                         "الطريقة": METHODS[pq.method], "التكلفة للقطعة": round(r["total"], 4),
                         "السعر المقترح للوحدة": round(r["price_unit"], 2), "السعر المعروض للوحدة": round(offered, 2),
                         "قيمة العرض": round(offered * d["qty"], 2), "الحالة": QUOTE_STATUSES.get(pq.status, pq.status),
                         "بواسطة": users.get(pq.created_by_id, "")})
    return pd.DataFrame(rows, columns=["id", "رقم العرض", "التاريخ", "العميل", "المنتج", "الكمية", "الوحدة", "الطريقة",
                                       "التكلفة للقطعة", "السعر المقترح للوحدة", "السعر المعروض للوحدة", "قيمة العرض",
                                       "الحالة", "بواسطة"])


def cost_card(d: dict) -> pd.DataFrame:
    """Per-piece cost card: materials then each cost element, one column per method."""
    res = calc(d)
    cols = [METHODS[m] for m in PRICE_FIELD]
    pieces = res[WAC]["pieces"]
    rows = []
    for l in d["lines"]:
        rows.append({"البند": l["name"], "الكمية للقطعة": f"{l['qty']:.4f} {l['unit']}",
                     "الكمية للطلبية": f"{l['qty'] * pieces:,.3f} {l['unit']}",
                     **{METHODS[m]: l["qty"] * l[PRICE_FIELD[m]] for m in PRICE_FIELD}})

    def add(label, key, extra=""):
        rows.append({"البند": label, "الكمية للقطعة": extra, "الكمية للطلبية": "", **{METHODS[m]: res[m][key] for m in PRICE_FIELD}})

    add("إجمالي الخامات", "materials")
    add("تكاليف تحويلية (أجور وطاقة وصيانة وأخرى)", "conversion")
    add("هالك طبيعي", "waste", f"{_f(d['waste_pct']):g}%")
    add("نصيب الأسطمبة", "mold", MOLD_MODES.get(d["mold_mode"], ""))
    add("تكلفة الإنتاج", "production")
    add("مصروفات عمومية وإدارية", "ga", _basis_label(d["ga_basis"], d["ga_value"]))
    add("مصروفات بيع وتسويق", "selling", _basis_label(d["selling_basis"], d["selling_value"]))
    add("التكلفة الكلية للقطعة", "total")
    add("الربح", "profit", f"هامش {_f(d['margin_pct']):g}% من السعر")
    add("Markup % على التكلفة", "markup_pct")
    add("السعر المقترح للقطعة", "price_piece")
    if _f(d["pieces_per_unit"]) != 1:
        add(f"السعر المقترح لل{d.get('unit', 'وحدة')}", "price_unit", f"{_f(d['pieces_per_unit']):g} قطعة")
    if d.get("piece_weight_g"):
        add("السعر المقترح للكيلو", "price_kg", f"وزن القطعة {_f(d['piece_weight_g']):g} جم")
    add("قيمة الطلبية بالسعر المقترح", "order_value", f"{res[WAC]['pieces']:g} قطعة")
    df = pd.DataFrame(rows, columns=["البند", "الكمية للقطعة", "الكمية للطلبية", *cols])
    for c in cols:
        df[c] = df[c].astype(float).round(4)
    return df


def _basis_label(basis: str, value) -> str:
    return f"{_f(value):g}%" if basis == PCT else f"{_f(value):g} للقطعة"

