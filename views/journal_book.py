"""Journal book: every approved entry, line by line."""
import streamlit as st

from core.services import ledger
from ui.common import date_range, export_buttons, guard, page_header

SCREEN = "journal_book"
MONEY = st.column_config.NumberColumn(format="%.2f")


def render() -> None:
    guard(SCREEN)
    page_header("دفتر اليومية", "القيود المرحّلة من المستندات المعتمدة")
    d1, d2 = date_range("jb")
    c1, c2 = st.columns([2, 1])
    query = c1.text_input("بحث (رقم قيد، مستند، حساب، بيان)", key="jb_q")
    include_void = c2.checkbox("إظهار القيود الملغاة", key="jb_void")
    df = ledger.journal_book(d1, d2, include_void)
    if query:
        mask = df[["رقم القيد", "المستند", "الحساب", "البيان"]].apply(
            lambda col: col.astype(str).str.contains(query, case=False, regex=False)).any(axis=1)
        df = df[mask]
    if df.empty:
        st.info("لا توجد قيود")
        return
    st.dataframe(df, hide_index=True, width="stretch", column_config={"مدين": MONEY, "دائن": MONEY})
    posted = df[df["الحالة"] == "مرحّل"]
    totals = {"مدين": float(posted["مدين"].sum()), "دائن": float(posted["دائن"].sum())}
    st.caption(f"عدد القيود: {df['رقم القيد'].nunique()} — إجمالي المدين {totals['مدين']:,.2f} — "
               f"إجمالي الدائن {totals['دائن']:,.2f}")
    export_buttons(df, "دفتر اليومية", SCREEN, "journal_book", subtitle=f"من {d1} إلى {d2}", totals=totals)
