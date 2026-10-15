"""Pricing: three-way material costing, conversion and overheads, margin, and quotes with revisions."""
from datetime import date

import pandas as pd
import streamlit as st

from core.models.costing import (BASES, ENERGY, GA, MAINTENANCE, METHODS, MOLD_LIFE, MOLD_MODES, MOLD_NONE, OTHER, PCT,
                                 Q_ACCEPTED, QUOTE_STATUSES, SELLING, WAGES)
from core.models.parties import CUSTOMER
from core.services import boms, cost_rates, items, parties, quotes as svc
from ui.common import export_buttons, flash, guard, page_header, run_action
from views.cost_card import show_card

SCREEN = svc.SCREEN
PRICE = st.column_config.NumberColumn(format="%.4f")
CONV = ((WAGES, "wages"), (ENERGY, "energy"), (MAINTENANCE, "maintenance"), (OTHER, "other"))


def _lines_editor(key: str, base: list[dict], comp_opts: dict) -> list[dict]:
    rows = [{"الخامة": comp_opts.get(l["component_id"]), "الكمية للقطعة": l["qty"], "السعر اليدوي": l["manual_price"]}
            for l in base] or [{"الخامة": None, "الكمية للقطعة": None, "السعر اليدوي": None}] * 2
    df = pd.DataFrame(rows).astype({"الكمية للقطعة": "float", "السعر اليدوي": "float"})
    edited = st.data_editor(
        # The editable grid lays out left to right; reverse so the first column sits on the right.
        df[df.columns[::-1]], key=key, num_rows="dynamic", hide_index=True, width="stretch",
        column_config={
            "الخامة": st.column_config.SelectboxColumn(options=list(comp_opts.values()), width="large"),
            "الكمية للقطعة": st.column_config.NumberColumn(min_value=0.0, format="%.4f",
                                                          help="بوحدة الخامة: 150 جرام من خامة بالكيلو = 0.15"),
            "السعر اليدوي": st.column_config.NumberColumn(min_value=0.0, format="%.4f",
                                                         help="سعر الوحدة اللي عايز تسعّر بيه في عمود «يدوي»"),
        },
    )
    ids = {v: k for k, v in comp_opts.items()}
    out = []
    for r in edited.to_dict("records"):
        cid = ids.get(r["الخامة"])
        if cid is None:
            continue
        out.append({"component_id": cid, "qty": r["الكمية للقطعة"] if pd.notna(r["الكمية للقطعة"]) else 0,
                    "manual_price": r["السعر اليدوي"] if pd.notna(r["السعر اليدوي"]) else None})
    return out


def _priced(lines: list[dict], snapshot: dict[int, dict], refresh: bool) -> list[dict]:
    """Attach WAC and last prices: the saved snapshot where there is one, today's prices otherwise."""
    need = [l["component_id"] for l in lines if refresh or l["component_id"] not in snapshot]
    live = boms.component_prices(need) if need else {}
    names = boms.item_labels([l["component_id"] for l in lines]) if lines else {}
    out = []
    for l in lines:
        cid = l["component_id"]
        if cid in live:
            wac, last = live[cid]
        else:
            wac, last = snapshot[cid]["wac_price"], snapshot[cid]["last_price"]
        manual = l["manual_price"] if l["manual_price"] is not None else (last or wac)
        name, unit = names.get(cid, ("", ""))
        out.append({"component_id": cid, "name": name, "unit": unit, "qty": float(l["qty"] or 0), "wac_price": wac,
                    "last_price": last, "manual_price": float(manual)})
    return out


def _overhead_inputs(col_basis, col_value, label: str, comp: str, v: dict, rates: dict, key: str):
    field = "ga" if comp == GA else "selling"
    rate = rates.get(comp, {"basis": PCT, "value": 0.0})
    basis0 = v.get(f"{field}_basis") or rate["basis"]
    bases = list(BASES)
    basis = col_basis.selectbox(f"{label}: الأساس", bases, format_func=BASES.get, index=bases.index(basis0),
                                key=f"{key}_{field}_b")
    value0 = v.get(f"{field}_value") if f"{field}_value" in v else (rate["value"] if rate["basis"] == basis else 0.0)
    value = col_value.number_input(f"{label}: {'النسبة %' if basis == PCT else 'للقطعة'}", min_value=0.0,
                                   value=float(value0), format="%.4f", key=f"{key}_{field}_v_{basis}")
    return basis, value


def _form(user, quote: dict | None, key: str) -> None:
    products = items.item_options(("finished",))
    if not products:
        st.warning("سجّل المنتجات التامة الأول من شاشة الأصناف")
        return
    v = quote or {}
    c1, c2, c3 = st.columns([2, 2, 1])
    pids = list(products)
    item_id = c1.selectbox("المنتج", pids, format_func=products.get, key=f"{key}_item", disabled=quote is not None,
                           index=pids.index(v["item_id"]) if v.get("item_id") in pids else None,
                           placeholder="اختار المنتج التام")
    if item_id is None:
        return
    k = f"{key}_{item_id}"
    defaults = boms.item_defaults(item_id)
    customers = parties.options(CUSTOMER)
    cids = list(customers)
    party_id = c2.selectbox("العميل (اختياري)", cids, format_func=customers.get, key=f"{k}_party",
                            index=cids.index(v["party_id"]) if v.get("party_id") in cids else None,
                            placeholder="عرض عام من غير عميل")
    quote_date = c3.date_input("التاريخ", value=v.get("quote_date") or date.today(), key=f"{k}_date",
                               format="YYYY-MM-DD")
    unit = defaults["unit"]
    c4, c5, c6, c7 = st.columns(4)
    qty = c4.number_input(f"الكمية المطلوبة ({unit})", min_value=0.0, step=100.0, value=float(v.get("qty") or 0),
                          key=f"{k}_qty")
    ppu = c5.number_input(f"عدد القطع في ال{unit}", min_value=0.001,
                          value=float(v.get("pieces_per_unit") or defaults["pieces_per_unit"]), key=f"{k}_ppu")
    weight = c6.number_input("وزن القطعة بالجرام", min_value=0.0,
                             value=float(v.get("piece_weight_g") or defaults["piece_weight_g"] or 0), key=f"{k}_w")
    methods = list(METHODS)
    method = c7.radio("الطريقة المعتمدة في العرض", methods, format_func=METHODS.get, key=f"{k}_method",
                      index=methods.index(v.get("method", methods[0])))

    st.markdown("##### الخامات للقطعة")
    if quote:
        base = quote["lines"]
        snapshot = {l["component_id"]: l for l in quote["lines"]}
        refresh = st.checkbox("تحديث أسعار المتوسط وآخر سعر من المخزن النهارده", key=f"{k}_refresh",
                              help="من غير العلامة الإصدار الجديد بياخد نفس أسعار الإصدار اللي قبله")
    else:
        base = [{"component_id": l["component_id"], "qty": l["qty"], "manual_price": l["manual"]}
                for l in defaults["lines"]]
        snapshot, refresh = {}, True
        if not defaults["has_bom"]:
            st.info("المنتج ملهوش تركيبة؛ ضيف خاماته هنا، أو سجّل التركيبة من شاشة التركيبات عشان تتملى تلقائي.")
    comp_opts = {i: lbl for i, lbl in items.item_options().items() if i != item_id}
    lines = _priced(_lines_editor(f"{k}_lines", base, comp_opts), snapshot, refresh)
    if lines:
        view = pd.DataFrame([{"الخامة": l["name"], "الكمية": l["qty"], "الوحدة": l["unit"],
                              "المتوسط": l["wac_price"], "آخر سعر": l["last_price"], "يدوي": l["manual_price"]}
                             for l in lines])
        st.dataframe(view, hide_index=True, width="stretch",
                     column_config={c: PRICE for c in ("الكمية", "المتوسط", "آخر سعر", "يدوي")})

    rates = cost_rates.rates_on(quote_date)
    st.markdown("##### التكاليف التحويلية للقطعة")
    conv = {}
    for col, (comp, field) in zip(st.columns(4), CONV):
        default = v[field] if field in v else rates.get(comp, {}).get("value", 0.0)
        conv[field] = col.number_input(cost_rates.RATE_COMPONENTS[comp], min_value=0.0, value=float(default),
                                       format="%.4f", key=f"{k}_{field}")
    if not quote and not any(c in rates for c, _ in CONV):
        st.caption("مفيش معدلات تحميل مسجلة؛ سجّلها من شاشة معدلات التحميل عشان تتملى تلقائي.")

    st.markdown("##### الهالك والأسطمبة")
    c8, c9 = st.columns([1, 3])
    waste = c8.number_input("الهالك الطبيعي %", min_value=0.0, max_value=99.0,
                            value=float(v.get("waste_pct", defaults["waste_pct"])), key=f"{k}_waste")
    modes = list(MOLD_MODES)
    mold_mode = c9.radio("نصيب الأسطمبة", modes, format_func=MOLD_MODES.get, horizontal=True, key=f"{k}_mold",
                         index=modes.index(v.get("mold_mode", MOLD_NONE)))
    mold_name, mold_cost, mold_life = v.get("mold_name") or "", float(v.get("mold_cost") or 0), \
        float(v.get("mold_life_pieces") or 0)
    if mold_mode != MOLD_NONE:
        c10, c11, c12 = st.columns(3)
        mold_name = c10.text_input("الأسطمبة", value=mold_name, key=f"{k}_mname")
        mold_cost = c11.number_input("تكلفة الأسطمبة", min_value=0.0, value=mold_cost, key=f"{k}_mcost")
        if mold_mode == MOLD_LIFE:
            mold_life = c12.number_input("العمر الافتراضي بالقطع", min_value=0.0, step=1000.0, value=mold_life,
                                         key=f"{k}_mlife")
        else:
            c12.caption("بتتقسم على عدد قطع الطلبية")

    st.markdown("##### المصروفات والهامش")
    c13, c14, c15, c16 = st.columns(4)
    ga_basis, ga_value = _overhead_inputs(c13, c14, "عمومية وإدارية", GA, v, rates, k)
    sell_basis, sell_value = _overhead_inputs(c15, c16, "بيع وتسويق", SELLING, v, rates, k)
    c17, c18, c19 = st.columns([1, 1, 2])
    margin = c17.number_input("هامش الربح % من السعر", min_value=0.0, max_value=99.0,
                              value=float(v.get("margin_pct") or 0), key=f"{k}_margin")
    offered = c18.number_input(f"السعر المعروض لل{unit} (اختياري)", min_value=0.0,
                               value=float(v.get("offered_price") or 0), key=f"{k}_offer",
                               help="لو اتفقت على سعر غير المقترح؛ الهامش الفعلي بيظهر تحت")
    notes = c19.text_input("ملاحظات", value=v.get("notes") or "", key=f"{k}_notes")

    data = {"quote_date": quote_date, "party_id": party_id, "item_id": item_id, "qty": qty, "pieces_per_unit": ppu,
            "piece_weight_g": weight or None, "method": method, "waste_pct": waste, **conv, "mold_mode": mold_mode,
            "mold_name": mold_name, "mold_cost": mold_cost, "mold_life_pieces": mold_life, "ga_basis": ga_basis,
            "ga_value": ga_value, "selling_basis": sell_basis, "selling_value": sell_value, "margin_pct": margin,
            "offered_price": offered or None, "notes": notes, "lines": lines}
    st.divider()
    st.subheader("النتيجة")
    if lines and qty:
        preview = dict(data, doc_no=v.get("doc_no", "عرض جديد"), unit=unit, item_name=products[item_id],
                       party_name=customers.get(party_id, ""))
        show_card(preview, f"{SCREEN}_draft")
    else:
        st.info("اكتب الكمية المطلوبة والخامات عشان تظهر التكلفة والسعر")
    st.caption("آخر سعر بيع لنفس العميل والمنتج هيظهر هنا بعد ما المبيعات تتبني في المرحلة 5.")
    label = "حفظ كإصدار جديد" if quote else "حفظ عرض السعر"
    if st.button(label, type="primary", key=f"{k}_save"):
        new_id = run_action(svc.save_quote, user, v.get("id"), data)
        if new_id:
            flash(f"تم حفظ العرض {svc.get_quote(new_id)['doc_no']}")
            for sk in [sk for sk in st.session_state if str(sk).startswith(key)]:
                del st.session_state[sk]
            st.rerun()


def _detail(user, qid: int) -> None:
    d = svc.get_quote(qid)
    revs = svc.revisions(d["quote_no"])
    if len(revs) > 1:
        st.caption("الإصدارات: " + " · ".join(
            f"{r['doc_no']} ({QUOTE_STATUSES[r['status']]}، {r['created_at']:%Y-%m-%d}، {r['by']})" for r in revs))
    view = st.radio("عرض", ["كارتة التكلفة", "تغيير الحالة", "تعديل (إصدار جديد)", "حذف"], horizontal=True,
                    key=f"pq_view_{qid}")
    if view == "كارتة التكلفة":
        show_card(d, SCREEN)
    elif view == "تغيير الحالة":
        if not (d["is_current"] and user.can(SCREEN, "edit")):
            st.info("الحالة بتتغير على آخر إصدار بس، ومحتاجة صلاحية التعديل")
            return
        sts = list(QUOTE_STATUSES)
        new = st.selectbox("الحالة", sts, format_func=QUOTE_STATUSES.get, index=sts.index(d["status"]),
                           key=f"pq_st_{qid}")
        if new == Q_ACCEPTED:
            st.caption("العرض المقبول بيتقفل؛ أي تعديل بعده يبقى عرض جديد.")
        if st.button("حفظ الحالة", key=f"pq_st_save_{qid}"):
            if run_action(svc.set_status, user, qid, new, success="تم تغيير الحالة"):
                st.rerun()
    elif view == "تعديل (إصدار جديد)":
        if not d["is_current"]:
            st.info("ده إصدار قديم؛ التعديل بيكون على آخر إصدار")
        elif d["status"] == Q_ACCEPTED:
            st.info("العرض اتقبل ومقفول؛ اعمل عرض جديد")
        elif not user.can(SCREEN, "edit"):
            st.info("ليس لديك صلاحية التعديل")
        else:
            st.caption(f"التعديل هيتحفظ كـ Q-{d['quote_no']:06d}-v{d['revision'] + 1}، و{d['doc_no']} هيفضل زي ما هو.")
            _form(user, d, f"pq_rev_{qid}")
    else:
        if not user.can(SCREEN, "delete"):
            st.info("ليس لديك صلاحية الحذف")
            return
        st.caption("بيتحذف آخر إصدار بس وهو مسودة؛ الإصدار اللي قبله يرجع هو الحالي.")
        if st.button("تأكيد الحذف", key=f"pq_del_{qid}"):
            if run_action(svc.delete_quote, user, qid, success="تم الحذف"):
                st.rerun()


def _list_tab(user) -> None:
    c1, c2, c3 = st.columns([1, 2, 1])
    statuses = {"الكل": None} | {v: k for k, v in QUOTE_STATUSES.items()}
    status = statuses[c1.selectbox("الحالة", list(statuses), key="pq_status")]
    customers = {None: "كل العملاء"} | parties.options(CUSTOMER, include_inactive=True)
    party = c2.selectbox("العميل", list(customers), format_func=customers.get, key="pq_party")
    all_revs = c3.checkbox("كل الإصدارات", key="pq_all")
    df = svc.quotes_frame(current_only=not all_revs, status=status, party_id=party)
    if df.empty:
        st.info("لا توجد عروض أسعار")
        return
    money = {c: st.column_config.NumberColumn(format="%.2f")
             for c in ("السعر المقترح للوحدة", "السعر المعروض للوحدة", "قيمة العرض")}
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch",
                 column_config=money | {"التكلفة للقطعة": PRICE})
    export_buttons(df.drop(columns=["id"]), "عروض الأسعار", SCREEN, "quotes")
    st.divider()
    labels = {int(r["id"]): f"{r['رقم العرض']} - {r['المنتج']} - {r['العميل'] or 'بدون عميل'} ({r['الحالة']})"
              for _, r in df.iterrows()}
    qid = st.selectbox("افتح عرض", list(labels), format_func=labels.get, key="pq_pick")
    _detail(user, qid)


def render() -> None:
    user = guard(SCREEN)
    page_header("التسعير وعروض الأسعار",
                "مقارنة ثلاثية للخامات (متوسط / آخر سعر / يدوي) + تكاليف تحويلية + أسطمبة + مصروفات + هامش")
    tabs = ["عروض الأسعار"] + (["عرض سعر جديد"] if user.can(SCREEN, "add") else [])
    handlers = {"عروض الأسعار": _list_tab, "عرض سعر جديد": lambda u: _form(u, None, "pq_new")}
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            handlers[name](user)
