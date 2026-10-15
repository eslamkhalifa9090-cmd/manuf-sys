"""Item card: every movement with running quantity, value and average."""
import streamlit as st

from core.services import items, stock
from ui.common import date_range, export_buttons, guard, page_header

SCREEN = "item_card"
MONEY = st.column_config.NumberColumn(format="%.2f")
PRICE = st.column_config.NumberColumn(format="%.4f")


def render() -> None:
    guard(SCREEN)
    page_header("كارت الصنف", "الوارد والمنصرف والرصيد ومتوسط التكلفة بعد كل حركة")
    opts = items.item_options(include_inactive=True)
    item_id = st.selectbox("الصنف", list(opts), format_func=opts.get, index=None, placeholder="اختار الصنف",
                           key="ic_item")
    d1, d2 = date_range("ic")
    if item_id is None:
        return
    df, info = stock.item_card(item_id, d1, d2)
    c1, c2, c3 = st.columns(3)
    c1.metric("الرصيد", f"{info['qty']:,.3f} {info['unit']}")
    c2.metric("القيمة", f"{info['value']:,.2f}")
    c3.metric("متوسط التكلفة", f"{(info['value'] / info['qty']) if info['qty'] else 0:,.4f}")
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={"سعر الوحدة": PRICE, "المتوسط": PRICE, "قيمة الوارد": MONEY, "قيمة المنصرف": MONEY,
                                "قيمة الرصيد": MONEY})
    export_buttons(df, f"كارت صنف {info['name']}", SCREEN, "item_card", subtitle=f"{info['code']} — من {d1} إلى {d2}")
