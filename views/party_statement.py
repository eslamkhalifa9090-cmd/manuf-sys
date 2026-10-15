"""Customer / supplier statement, balances and control-account reconciliation."""
from datetime import date

import streamlit as st

from core.models.parties import CUSTOMER, SUPPLIER
from core.services import parties as svc
from ui.common import date_range, export_buttons, guard, page_header

SCREEN = "party_statement"
MONEY = st.column_config.NumberColumn(format="%.2f")


def _statement_tab() -> None:
    opts = svc.options(include_inactive=True)
    pid = st.selectbox("العميل / المورد", list(opts), format_func=opts.get, index=None, placeholder="اختار",
                       key="ps_party")
    d1, d2 = date_range("ps")
    if pid is None:
        return
    df, info = svc.statement(pid, d1, d2)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("رصيد أول المدة", f"{info['opening']:,.2f}")
    c2.metric("مدين", f"{info['debit']:,.2f}")
    c3.metric("دائن", f"{info['credit']:,.2f}")
    c4.metric("الرصيد", f"{info['closing']:,.2f}")
    st.caption("الرصيد الموجب = مستحق على العميل، والسالب = مستحق للمورد.")
    st.dataframe(df, hide_index=True, width="stretch", column_config={"مدين": MONEY, "دائن": MONEY, "الرصيد": MONEY})
    export_buttons(df, f"كشف حساب {info['name']}", SCREEN, "party_statement", subtitle=f"من {d1} إلى {d2}",
                   totals={"مدين": info["debit"], "دائن": info["credit"], "الرصيد": info["closing"]})


def _balances_tab() -> None:
    c1, c2, c3 = st.columns(3)
    kind = {"العملاء": CUSTOMER, "الموردين": SUPPLIER}[c1.radio("النوع", ["العملاء", "الموردين"], horizontal=True,
                                                               key="pb_kind")]
    as_of = c2.date_input("الأرصدة حتى", value=date.today(), key="pb_asof", format="YYYY-MM-DD")
    hide_zero = c3.checkbox("إخفاء الأرصدة الصفرية", value=True, key="pb_zero")
    df = svc.balances_frame(kind, as_of)
    if hide_zero:
        df = df[df["الرصيد"] != 0]
    view = df.drop(columns=["party_id"])
    st.dataframe(view, hide_index=True, width="stretch", column_config={"الرصيد": MONEY})
    total = float(view["الرصيد"].sum()) if len(view) else 0.0
    st.metric("الإجمالي", f"{total:,.2f}")
    st.caption("رصيد العميل = المستحق عليه، ورصيد المورد = المستحق له.")
    title = "أرصدة العملاء" if kind == CUSTOMER else "أرصدة الموردين"
    export_buttons(view, title, SCREEN, f"balances_{kind}", subtitle=f"حتى {as_of}", totals={"الرصيد": total})
    with st.expander("مطابقة الحساب الإجمالي مع الأرصدة الفرعية"):
        st.dataframe(svc.reconciliation(as_of), hide_index=True, width="stretch")
        st.caption("لو فيه رصيد «بدون عميل/مورد» يبقى قيود قديمة اتسجلت على 1401 أو 2101 من غير اسم.")


def render() -> None:
    guard(SCREEN)
    page_header("كشف حساب عميل / مورد", "من القيود المعتمدة على حسابي العملاء والموردين")
    t1, t2 = st.tabs(["كشف حساب", "الأرصدة"])
    with t1:
        _statement_tab()
    with t2:
        _balances_tab()
