"""Predetermined conversion and overhead rates, with suggestions from actual expenses."""
from datetime import date

import streamlit as st

from core.models.costing import BASES, CONVERSION, GA, PCT, PER_PIECE, RATE_COMPONENTS, SELLING
from core.services import cost_rates as svc
from ui.common import export_buttons, flash, guard, page_header, run_action

SCREEN = svc.SCREEN


def _current() -> None:
    rates = svc.rates_on()
    cols = st.columns(len(RATE_COMPONENTS))
    for col, (comp, label) in zip(cols, RATE_COMPONENTS.items()):
        r = rates.get(comp)
        if r is None:
            col.metric(label, "—")
        else:
            val = f"{r['value']:g}%" if r["basis"] == PCT else f"{r['value']:,.4f}"
            col.metric(label, val, help=f"ساري من {r['valid_from']}")


def _add_form(user) -> None:
    with st.form("cr_add", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        comp = c1.selectbox("البند", list(RATE_COMPONENTS), format_func=RATE_COMPONENTS.get)
        basis = c2.selectbox("الأساس (للمصروفات بس)", list(BASES), format_func=BASES.get,
                             help="التكاليف التحويلية دايماً مبلغ للقطعة")
        value = c3.number_input("المعدل", min_value=0.0, format="%.4f")
        valid_from = c4.date_input("ساري من", value=date.today(), format="YYYY-MM-DD")
        notes = st.text_input("ملاحظات (مثلاً أساس الحساب)")
        if st.form_submit_button("حفظ المعدل", type="primary"):
            if comp in CONVERSION:
                basis = PER_PIECE
            if run_action(svc.save_rate, user, comp, value, valid_from, basis, notes, success="تم حفظ المعدل"):
                st.rerun()


def _suggest(user) -> None:
    st.caption("المقترح = المصروفات الفعلية المعتمدة في الأستاذ العام خلال الفترة ÷ عدد القطع المنتجة فيها. "
               "عدد القطع بيتكتب يدوي لحد ما أوامر الإنتاج تتبني في المرحلة 5. "
               "نسبة المصروفات العمومية والبيعية محسوبة من (التكاليف التحويلية + تكلفة المبيعات) في نفس الفترة.")
    d1, d2 = svc.default_period()
    c1, c2, c3 = st.columns(3)
    date_from = c1.date_input("من", value=d1, key="cr_from", format="YYYY-MM-DD")
    date_to = c2.date_input("إلى", value=d2, key="cr_to", format="YYYY-MM-DD")
    pieces = c3.number_input("عدد القطع المنتجة في الفترة", min_value=0.0, step=100.0, key="cr_pieces")
    df = svc.suggestions(date_from, date_to, pieces)
    st.dataframe(df.drop(columns=["component"]), hide_index=True, width="stretch",
                 column_config={"الفعلي في الفترة": st.column_config.NumberColumn(format="%.2f"),
                                "المقترح للقطعة": st.column_config.NumberColumn(format="%.4f"),
                                "المقترح %": st.column_config.NumberColumn(format="%.2f")})
    if not (pieces and user.can(SCREEN, "add")):
        return
    c4, c5 = st.columns([1, 2])
    valid_from = c4.date_input("تسري من", value=date.today(), key="cr_apply_from", format="YYYY-MM-DD")
    overhead = c5.radio("المصروفات العمومية والبيعية تتحمل", [PCT, PER_PIECE], format_func=BASES.get, horizontal=True,
                        key="cr_apply_basis")
    if st.button("حفظ المقترحات كمعدلات سارية", key="cr_apply"):
        rows = df.set_index("component")
        for comp in RATE_COMPONENTS:
            if comp in (GA, SELLING) and overhead == PCT:
                value, basis = rows.loc[comp, "المقترح %"], PCT
            else:
                value, basis = rows.loc[comp, "المقترح للقطعة"], PER_PIECE
            if value is None or value != value:
                continue
            if not run_action(svc.save_rate, user, comp, float(value), valid_from, basis,
                              f"مقترح من {date_from} إلى {date_to} على {pieces:g} قطعة"):
                return
        flash("تم حفظ المعدلات")
        st.rerun()


def _history(user) -> None:
    df = svc.rates_frame()
    if df.empty:
        st.info("لا توجد معدلات مسجلة")
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch",
                 column_config={"المعدل": st.column_config.NumberColumn(format="%.4f")})
    export_buttons(df.drop(columns=["id"]), "معدلات التحميل", SCREEN, "cost_rates")
    if user.can(SCREEN, "delete"):
        with st.expander("حذف معدل متسجل غلط"):
            labels = {int(r["id"]): f"{r['البند']} - {r['المعدل']:g} - من {r['ساري من']}" for _, r in df.iterrows()}
            rid = st.selectbox("المعدل", list(labels), format_func=labels.get, key="cr_del_pick")
            if st.button("تأكيد الحذف", key="cr_del"):
                if run_action(svc.delete_rate, user, rid, success="تم الحذف"):
                    st.rerun()


def render() -> None:
    user = guard(SCREEN)
    page_header("معدلات التحميل", "معدلات محددة مقدماً للتكاليف التحويلية والمصروفات؛ كل معدل ساري من تاريخه")
    st.subheader("المعدلات السارية النهارده")
    _current()
    tabs = ["سجل المعدلات"] + (["معدل جديد"] if user.can(SCREEN, "add") else []) + ["اقتراح من المصروفات الفعلية"]
    handlers = {"سجل المعدلات": _history, "معدل جديد": _add_form, "اقتراح من المصروفات الفعلية": _suggest}
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            handlers[name](user)
