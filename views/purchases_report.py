"""Approved purchases, line by line, with totals by supplier and item."""
import streamlit as st

from core.services import purchases
from ui.common import date_range, export_buttons, guard, page_header

SCREEN = "purchases_report"
MONEY = st.column_config.NumberColumn(format="%.2f")


def render() -> None:
    guard(SCREEN)
    page_header("تقرير المشتريات", "من الفواتير والمرتجعات المعتمدة؛ المرتجع بالسالب")
    d1, d2 = date_range("prp")
    df = purchases.lines_report(d1, d2)
    if df.empty:
        st.info("لا توجد مشتريات معتمدة في الفترة")
        return
    view = st.radio("العرض", ["تفصيلي", "حسب المورد", "حسب الصنف"], horizontal=True, key="prp_view")
    if view == "حسب المورد":
        df = df.groupby("المورد", as_index=False)["الصافي"].sum().sort_values("الصافي", ascending=False)
    elif view == "حسب الصنف":
        df = (df.groupby(["الصنف / الحساب", "الوحدة"], as_index=False)[["الكمية", "الصافي"]].sum()
              .sort_values("الصافي", ascending=False))
        df["متوسط السعر"] = (df["الصافي"] / df["الكمية"]).where(df["الكمية"] != 0).round(4)
    st.dataframe(df, hide_index=True, width="stretch", column_config={"الصافي": MONEY})
    total = float(df["الصافي"].sum())
    st.metric("صافي المشتريات", f"{total:,.2f}")
    export_buttons(df, f"تقرير المشتريات — {view}", SCREEN, "purchases_report", subtitle=f"من {d1} إلى {d2}",
                   totals={"الصافي": total})
