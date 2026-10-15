"""Stock balances at weighted average cost, with the GL reconciliation."""
from datetime import date

import streamlit as st

from core.services import items, stock
from ui.common import export_buttons, guard, page_header

SCREEN = "stock_report"
MONEY = st.column_config.NumberColumn(format="%.2f")


def render() -> None:
    guard(SCREEN)
    page_header("أرصدة المخزون", "الكمية والقيمة بالمتوسط المرجح، ومعاها آخر سعر شراء للمقارنة")
    groups = {None: "كل المجموعات"} | items.group_options(include_inactive=True)
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    gid = c1.selectbox("المجموعة", list(groups), format_func=groups.get, key="sr_group")
    as_of = c2.date_input("حتى تاريخ", value=date.today(), key="sr_asof", format="YYYY-MM-DD")
    hide_zero = c3.checkbox("إخفاء الصفري", value=True, key="sr_zero")
    below = c4.checkbox("تحت حد الطلب فقط", key="sr_below")
    df = stock.balances_frame(as_of, gid, hide_zero)
    if below:
        df = df[df["تحت حد الطلب"]]
    if df.empty:
        st.info("لا توجد أرصدة")
        return
    view = df.drop(columns=["item_id"])
    st.dataframe(view, hide_index=True, width="stretch",
                 column_config={"القيمة": MONEY, "متوسط التكلفة": st.column_config.NumberColumn(format="%.4f"),
                                "آخر سعر شراء": st.column_config.NumberColumn(format="%.4f")})
    total = float(df["القيمة"].sum())
    st.metric("إجمالي قيمة المخزون", f"{total:,.2f}")
    export_buttons(view, "أرصدة المخزون", SCREEN, "stock_balances", subtitle=f"حتى {as_of} — {groups[gid]}",
                   totals={"القيمة": total})
    with st.expander("مطابقة قيمة المخزون مع حسابات الأستاذ العام"):
        rec = stock.reconciliation(as_of)
        st.dataframe(rec, hide_index=True, width="stretch", column_config={c: MONEY for c in rec.columns[1:]})
        if len(rec) and (rec["الفرق"] != 0).any():
            st.warning("فيه فرق: غالباً قيد يدوي اتسجل على حساب مخزون. الحركات المخزنية لازم تتعمل من أذون المخزن.")
        else:
            st.success("قيمة المخزون مطابقة لحسابات المخزون ✓")
