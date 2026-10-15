"""Production: compound runs (stage 1), production orders (stage 2), the month-end overhead close and reports."""
from datetime import date

import pandas as pd
import streamlit as st

from core.models.costing import RATE_COMPONENTS as COMPONENT_LABELS
from core.models.documents import STATUS_LABELS
from core.services import approval, boms, items, molds, production as svc, sales
from ui.common import date_range, export_buttons, guard, page_header, run_action
from views.sales import _clear, _init, _num, _pick

EDITABLE = (STATUS_LABELS["pending"], STATUS_LABELS["returned"])
MONEY = st.column_config.NumberColumn(format="%.2f")


def _set_lines(key: str, lines: list[dict]) -> None:
    st.session_state[f"{key}_base"] = lines
    st.session_state[f"{key}_ver"] = st.session_state.get(f"{key}_ver", 0) + 1


def _materials_grid(key: str) -> list[dict]:
    item_opts = items.item_options(("raw", "compound", "packaging", "accessories", "samples", "spares"))
    base = st.session_state.get(f"{key}_base") or []
    rows = [{"الخامة": item_opts.get(l["item_id"]), "الكمية": l["qty"]} for l in base]
    if not rows:
        rows = [{"الخامة": None, "الكمية": None}] * 3
    df = pd.DataFrame(rows).astype({"الكمية": "float"})
    edited = st.data_editor(
        df[df.columns[::-1]], key=f"{key}_ed_{st.session_state.get(f'{key}_ver', 0)}", num_rows="dynamic",
        hide_index=True, width="stretch",
        column_config={"الخامة": st.column_config.SelectboxColumn(options=list(item_opts.values()), width="large"),
                       "الكمية": st.column_config.NumberColumn(min_value=0.0, format="%.4f")})
    ids = {v: k for k, v in item_opts.items()}
    return [{"item_id": ids.get(r["الخامة"]), "qty": _num(r["الكمية"])} for r in edited.to_dict("records")]


def _kilos(lines: list[dict]) -> float:
    units = {i: u for i, (_, u) in boms.item_labels([l["item_id"] for l in lines if l["item_id"]]).items()}
    return sum(l["qty"] for l in lines if l["item_id"] and boms.grams_per_unit(units.get(l["item_id"], "")))


def _journal(doc_type: str, doc_id: int) -> None:
    with st.expander("القيد"):
        try:
            st.dataframe(approval.preview(doc_type, doc_id), hide_index=True, width="stretch",
                         column_config={"مدين": MONEY, "دائن": MONEY})
        except Exception as exc:  # e.g. not enough stock yet: say why instead of failing the page
            st.warning(str(exc))


def _doc_actions(user, screen: str, doc_type: str, doc_id: int, status: str, delete_fn, edit_fn) -> None:
    _journal(doc_type, doc_id)
    if status not in EDITABLE:
        return
    if user.can(screen, "edit"):
        with st.expander("تعديل المستند"):
            edit_fn()
    if user.can(screen, "delete"):
        with st.expander("حذف المستند"):
            if st.button("تأكيد الحذف", key=f"{doc_type}_del_{doc_id}"):
                if run_action(delete_fn, user, doc_id, success="تم الحذف"):
                    st.rerun()


# ---------------------------------------------------------------- stage 1: compound runs

def _run_form(user, run: dict | None, key: str) -> None:
    outputs = items.item_options(("compound",))
    if not outputs:
        st.warning("سجّل صنف التضريبة الأول في مجموعة «تضريبة وخامات وسيطة»")
        return
    v = run or {}
    if f"{key}_base" not in st.session_state:
        _set_lines(key, v.get("lines", []))
    _init(f"{key}_out", v.get("output_item_id"))
    _init(f"{key}_qty", float(v.get("output_qty") or 0))
    _init(f"{key}_rate", float(v.get("conversion_rate") or 0))
    c1, c2, c3, c4 = st.columns(4)
    out = _pick("التضريبة الناتجة", outputs, f"{key}_out", c1)
    doc_date = c2.date_input("التاريخ", value=v.get("doc_date") or date.today(), format="YYYY-MM-DD", key=f"{key}_date")
    qty = c3.number_input("الكمية الناتجة (كيلو)", min_value=0.0, step=10.0, key=f"{key}_qty")
    rate = c4.number_input("تحميل للكيلو (اختياري)", min_value=0.0, step=0.5, format="%.4f", key=f"{key}_rate",
                           help="أجور وطاقة الخلط لو حابب تحمّلها على التضريبة؛ صفر = بالخامات بس")
    if st.button("حمّل التركيبة على الكمية", key=f"{key}_recipe", disabled=not (out and qty)):
        lines = svc.recipe_inputs(out, qty)
        if not lines:
            st.warning("الصنف ده ملوش تركيبة؛ اكتب الخامات بإيدك")
        else:
            _set_lines(key, lines)
            st.rerun()
    lines = _materials_grid(key)
    kilos = _kilos(lines)
    k = st.columns(3)
    k[0].metric("وزن الخامات", f"{kilos:,.3f}")
    k[1].metric("الناتج", f"{qty:,.3f}")
    k[2].metric("فرق الوزن (فاقد الخلط)", f"{kilos - qty:,.3f}",
                f"{(kilos - qty) / kilos * 100:.1f}%" if kilos else None, delta_color="inverse")
    memo = st.text_input("البيان", value=v.get("memo", ""), key=f"{key}_memo")
    if st.button("حفظ وإرسال للاعتماد", type="primary", key=f"{key}_save"):
        if out is None:
            st.error("اختار التضريبة الناتجة")
            return
        if run_action(svc.save_run, user, v.get("id"), doc_date, out, qty, lines, rate, memo,
                      success="تم الحفظ. الخامات هتتصرف والتضريبة هتدخل المخزن بعد الاعتماد."):
            _clear(key)
            st.rerun()


def render_compound_runs() -> None:
    user = guard(svc.COMPOUND_SCREEN)
    page_header("تشغيلات التضريبة", "المرحلة الأولى: الخامات بتتصرف بمتوسط التكلفة على تحت التشغيل 1303 والتضريبة "
                "بتدخل المخزن بإجمالي تكلفتها")
    if user.can(svc.COMPOUND_SCREEN, "add"):
        with st.expander("تشغيلة جديدة", expanded=True):
            _run_form(user, None, "cr_new")
    df = svc.runs_frame(user)
    if df.empty:
        st.info("مفيش تشغيلات")
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch")
    export_buttons(df.drop(columns=["id"]), "تشغيلات التضريبة", svc.COMPOUND_SCREEN, "compound_runs")
    labels = {int(r["id"]): f"{r['رقم المستند']} - {r['الناتج']} {r['الكمية']:g} ({r['الحالة']})"
              for _, r in df.iterrows()}
    rid = st.selectbox("تفاصيل التشغيلة", list(labels), format_func=labels.get, key="cr_pick")
    run = svc.get_run(rid)
    _doc_actions(user, svc.COMPOUND_SCREEN, "compound_run", rid, STATUS_LABELS[run["status"]], svc.delete_run,
                 lambda: _run_form(user, run, f"cr_edit_{rid}"))


# ---------------------------------------------------------------- stage 2: production orders

def _fill_defaults(key: str, item: int, pieces: float, on_date: date) -> None:
    """Runs before the widgets are drawn, so it may set their values."""
    d = svc.order_defaults(item, pieces, on_date)
    _set_lines(key, d["lines"])
    st.session_state[f"{key}_nwaste"] = d["normal_waste_pct"]
    for c, val in d["rates"].items():
        st.session_state[f"{key}_{c}"] = val


def _order_form(user, order: dict | None, key: str) -> None:
    products = items.item_options(("finished",))
    if not products:
        st.warning("سجّل المنتجات الأول في مجموعة «إنتاج تام»")
        return
    v = order or {}
    if f"{key}_base" not in st.session_state:
        _set_lines(key, v.get("lines", []))
    rates = v.get("rates", {})
    for f, val in [("item", v.get("item_id")), ("so", v.get("sales_order_id")), ("mold", v.get("mold_id")),
                   ("good", float(v.get("good_pieces") or 0)), ("waste", float(v.get("waste_pieces") or 0)),
                   ("nwaste", float(v.get("normal_waste_pct") or 0)), ("shots", float(v.get("shots") or 0)),
                   ("cav", int(v.get("cavities") or 1))] + [(c, float(rates.get(c, 0))) for c in svc.RATE_FIELDS]:
        _init(f"{key}_{f}", val)
    c1, c2, c3 = st.columns(3)
    item = _pick("المنتج", products, f"{key}_item", c1)
    doc_date = c2.date_input("التاريخ", value=v.get("doc_date") or date.today(), format="YYYY-MM-DD", key=f"{key}_date")
    orders = sales.order_options()
    if v.get("sales_order_id") and v["sales_order_id"] not in orders:
        orders = sales.order_options(open_only=False)
    so = _pick("أمر العميل (اختياري)", orders, f"{key}_so", c3, "إنتاج للمخزن")
    c4, c5, c6 = st.columns(3)
    good = c4.number_input("القطع السليمة", min_value=0.0, step=100.0, format="%.0f", key=f"{key}_good")
    waste = c5.number_input("قطع الهالك", min_value=0.0, step=10.0, format="%.0f", key=f"{key}_waste")
    nwaste = c6.number_input("نسبة الهالك الطبيعي %", min_value=0.0, step=0.5, key=f"{key}_nwaste",
                             help="من القطع السليمة؛ الزيادة عنها هالك غير طبيعي بيروح على 5402")
    if item:
        unit, ppu = items_unit(item)
        if ppu != 1:
            st.caption(f"{good:,.0f} قطعة = {good / ppu:,.2f} {unit}")
    mold_opts = molds.mold_options()
    c7, c8, c9 = st.columns(3)
    mold_id = _pick("الأسطمبة", mold_opts, f"{key}_mold", c7, "من غير أسطمبة")
    shots = cav = 0
    if mold_id:
        m = molds.get_mold(mold_id)
        st.session_state[f"{key}_cav"] = min(max(int(st.session_state[f"{key}_cav"]), 1), m["cavities"])
        shots = c8.number_input("عدد الكبسات", min_value=0.0, step=10.0, format="%.0f", key=f"{key}_shots")
        cav = c9.number_input(f"التجاويف الشغالة (من {m['cavities']})", min_value=1, max_value=m["cavities"], step=1,
                              key=f"{key}_cav")
        if shots and abs(shots * cav - (good + waste)) > 0.5:
            st.warning(f"الكبسات × التجاويف = {shots * cav:,.0f} قطعة، والسليم + الهالك = {good + waste:,.0f}")
    st.markdown("**معدلات التحميل للقطعة (من شاشة معدلات التحميل، وتقدر تعدّلها)**")
    rc = st.columns(len(svc.RATE_FIELDS))
    rate_vals = {c: col.number_input(COMPONENT_LABELS.get(c, c), min_value=0.0, step=0.05, format="%.4f",
                                     key=f"{key}_{c}") for col, c in zip(rc, svc.RATE_FIELDS)}
    st.button("حمّل الافتراضي من التركيبة والمعدلات", key=f"{key}_defaults", disabled=not (item and good + waste),
              on_click=_fill_defaults, args=(key, item, good + waste, doc_date),
              help="الخامات = التركيبة × (السليم + الهالك)، ونسبة الهالك من التركيبة، والمعدلات السارية في التاريخ")
    lines = _materials_grid(key)
    memo = st.text_input("البيان", value=v.get("memo", ""), key=f"{key}_memo")
    if st.button("حفظ وإرسال للاعتماد", type="primary", key=f"{key}_save"):
        if item is None:
            st.error("اختار المنتج")
            return
        if run_action(svc.save_order, user, v.get("id"), doc_date, item, good, lines, waste, nwaste, rate_vals, so,
                      mold_id, shots, cav or None, memo,
                      success="تم الحفظ. الخامات والمنتج والأسطمبة بيتأثروا بعد الاعتماد."):
            _clear(key)
            st.rerun()


def items_unit(item_id: int) -> tuple[str, float]:
    d = boms.item_defaults(item_id)
    return d.get("unit", ""), float(d.get("pieces_per_unit") or 1)


def _costing_card(order_id: int) -> None:
    try:
        c = svc.preview_costing(order_id)
    except Exception as exc:
        st.warning(str(exc))
        return
    k = st.columns(4)
    k[0].metric("الخامات", f"{c['material']:,.2f}")
    k[1].metric("التحميل", f"{c['conversion']:,.2f}")
    k[2].metric("إهلاك الأسطمبة", f"{c['mold_amount']:,.2f}",
                f"{c['mold_units']:g} وحدة × {c['mold_rate']:,.4f}" if c["mold_units"] else None, delta_color="off")
    k[3].metric("الإجمالي", f"{c['total']:,.2f}")
    k = st.columns(4)
    k[0].metric("هالك غير طبيعي (5402)", f"{c['abnormal']:,.2f}", f"{c['abnormal_pieces']:g} قطعة", delta_color="off")
    k[1].metric("للإنتاج التام", f"{c['finished']:,.2f}")
    k[2].metric("تكلفة القطعة", f"{c['cost_per_piece']:,.4f}")
    k[3].metric("تكلفة الوحدة", f"{c['cost_per_unit']:,.4f}")


def render_production_orders() -> None:
    user = guard(svc.PRODUCTION_SCREEN)
    page_header("أوامر الإنتاج", "المرحلة التانية: خامات + تحميل بالمعدلات (5301) + إهلاك الأسطمبة على عدد الكبسات "
                "(1206) على تحت التشغيل 1303، والسليم بيدخل مخزن الإنتاج التام")
    if user.can(svc.PRODUCTION_SCREEN, "add"):
        with st.expander("أمر إنتاج جديد", expanded=True):
            _order_form(user, None, "po_new")
    df = svc.orders_frame(user)
    if df.empty:
        st.info("مفيش أوامر إنتاج")
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch",
                 column_config={"إهلاك الأسطمبة": MONEY, "تكلفة الإنتاج التام": MONEY})
    export_buttons(df.drop(columns=["id"]), "أوامر الإنتاج", svc.PRODUCTION_SCREEN, "production_orders")
    labels = {int(r["id"]): f"{r['رقم الأمر']} - {r['المنتج']} - {r['قطع سليمة']:,.0f} قطعة ({r['الحالة']})"
              for _, r in df.iterrows()}
    oid = st.selectbox("تفاصيل الأمر", list(labels), format_func=labels.get, key="po_pick")
    order = svc.get_order(oid)
    status = STATUS_LABELS[order["status"]]
    if status in EDITABLE:
        st.markdown("**التكلفة لو اتعتمد دلوقتي**")
        _costing_card(oid)
    _doc_actions(user, svc.PRODUCTION_SCREEN, "production_order", oid, status, svc.delete_order,
                 lambda: _order_form(user, order, f"po_edit_{oid}"))


# ---------------------------------------------------------------- month-end overhead close

def render_overhead_close() -> None:
    user = guard(svc.CLOSE_SCREEN)
    page_header("إقفال الأعباء الشهري", "التحميل بالمعدلات (5301) قصاد المصروفات الفعلية (5201-5208)؛ الفرق "
                "كله بيتقفل على فروق التكلفة 5404")
    if user.can(svc.CLOSE_SCREEN, "add"):
        with st.container(border=True):
            today = date.today()
            prev = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
            c1, c2, _ = st.columns([1, 1, 3])
            year = c1.number_input("السنة", min_value=2000, max_value=2100, value=prev[0], step=1, key="oc_year")
            month = c2.number_input("الشهر", min_value=1, max_value=12, value=prev[1], step=1, key="oc_month")
            f = svc.close_preview(int(year), int(month))
            if f["actual"]:
                st.dataframe(pd.DataFrame([{"الحساب": f"{a['code']} - {a['name']}", "الفعلي": a["amount"]}
                                           for a in f["actual"]]), hide_index=True, width="stretch",
                             column_config={"الفعلي": MONEY})
            k = st.columns(3)
            k[0].metric("المحمّل على الإنتاج", f"{f['applied']:,.2f}")
            k[1].metric("الفعلي", f"{f['total_actual']:,.2f}")
            k[2].metric("الفرق لـ 5404", f"{f['variance']:,.2f}",
                        "تحميل بأقل من الفعلي" if f["variance"] > 0 else ("تحميل بأكتر من الفعلي" if f["variance"] < 0
                                                                          else None), delta_color="off")
            st.caption("بعد اعتماد الإقفال، أوامر الإنتاج والتضريبة في الشهر ده بتتقفل لحد ما يتلغي اعتماده")
            note = st.text_input("ملاحظة", key="oc_note")
            if st.button("احسب وابعت للاعتماد", type="primary", key="oc_compute"):
                if run_action(svc.compute_close, user, int(year), int(month), note,
                              success="تم حساب الإقفال. في انتظار الاعتماد."):
                    st.rerun()
    df = svc.closes_frame(user)
    if df.empty:
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch",
                 column_config={c: MONEY for c in ("المحمّل", "الفعلي", "الفرق")})
    export_buttons(df.drop(columns=["id"]), "إقفال الأعباء الشهري", svc.CLOSE_SCREEN, "overhead_close")
    labels = {int(r["id"]): f"{r['رقم المستند']} - {r['الشهر']} ({r['الحالة']})" for _, r in df.iterrows()}
    doc_id = st.selectbox("تفاصيل المستند", list(labels), format_func=labels.get, key="oc_pick")
    status = df.loc[df["id"] == doc_id, "الحالة"].iloc[0]
    _journal("overhead_close", doc_id)
    if status in EDITABLE and user.can(svc.CLOSE_SCREEN, "delete"):
        if st.button("حذف المستند", key=f"oc_del_{doc_id}"):
            if run_action(svc.delete_close, user, doc_id, success="تم الحذف"):
                st.rerun()


# ---------------------------------------------------------------- reports

def render_report() -> None:
    user = guard("production_report")
    page_header("تقارير الإنتاج والربحية", "الإنتاج الفعلي وتكلفته، حالة أوامر العملاء، وربحية كل أمر قصاد عرض السعر")
    d1, d2 = date_range("prr")
    tab_prod, tab_orders, tab_profit = st.tabs(["الإنتاج", "حالة أوامر العملاء", "ربحية الأوامر"])
    with tab_prod:
        df = svc.orders_frame(user, d1, d2, approved_only=True).drop(columns=["id", "الحالة", "ملاحظة المراجع"])
        if df.empty:
            st.info("مفيش إنتاج معتمد في الفترة")
        else:
            st.dataframe(df, hide_index=True, width="stretch",
                         column_config={"إهلاك الأسطمبة": MONEY, "تكلفة الإنتاج التام": MONEY})
            k = st.columns(3)
            k[0].metric("قطع سليمة", f"{df['قطع سليمة'].sum():,.0f}")
            run = df["قطع سليمة"].sum() + df["هالك"].sum()
            k[1].metric("% الهالك", f"{df['هالك'].sum() / run * 100:.2f}%" if run else "-")
            k[2].metric("تكلفة الإنتاج التام", f"{df['تكلفة الإنتاج التام'].sum():,.2f}")
            export_buttons(df, "تقرير الإنتاج", "production_report", "production",
                           subtitle=f"من {d1} إلى {d2}", totals={"تكلفة الإنتاج التام":
                                                                  float(df["تكلفة الإنتاج التام"].sum())})
    with tab_orders:
        df = sales.orders_frame()
        df = df[(df["التاريخ"] >= d1) & (df["التاريخ"] <= d2)] if not df.empty else df
        if df.empty:
            st.info("مفيش أوامر عملاء في الفترة")
        else:
            rows = []
            for _, o in df.iterrows():
                for _, p in sales.order_progress(int(o["id"])).iterrows():
                    rows.append({"رقم الأمر": o["رقم الأمر"], "العميل": o["العميل"], "تاريخ التسليم": o["تاريخ التسليم"],
                                 "حالة الأمر": o["الحالة"], "الصنف": p["الصنف"], "المطلوب": p["المطلوب"],
                                 "اتنتج": p["اتنتج"], "اتسلّم": p["اتسلّم"], "الباقي": p["الباقي"]})
            det = pd.DataFrame(rows)
            st.dataframe(det, hide_index=True, width="stretch")
            export_buttons(det, "حالة أوامر العملاء", "production_report", "order_status",
                           subtitle=f"من {d1} إلى {d2}")
    with tab_profit:
        df = svc.order_profit_frame(d1, d2)
        if df.empty:
            st.info("مفيش أوامر عملاء في الفترة")
        else:
            st.caption("المبيعات والتكلفة من الفواتير المعتمدة على الأمر؛ تكلفة الإنتاج الفعلية من أوامر الإنتاج "
                       "المربوطة بيه، والعرض من عرض السعر اللي الأمر اتعمل منه")
            st.dataframe(df, hide_index=True, width="stretch",
                         column_config={c: MONEY for c in ("صافي المبيعات", "تكلفة المبيعات", "مجمل الربح")})
            export_buttons(df, "ربحية أوامر العملاء", "production_report", "order_profit",
                           subtitle=f"من {d1} إلى {d2}",
                           totals={c: float(df[c].sum()) for c in ("صافي المبيعات", "تكلفة المبيعات", "مجمل الربح")})
