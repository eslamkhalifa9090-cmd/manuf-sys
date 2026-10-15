"""Trial balance: opening, period movement and closing balances with totals."""
import pandas as pd
import streamlit as st

from core.services import ledger
from ui.common import date_range, export_buttons, guard, page_header, run_action

SCREEN = "trial_balance"
LEVELS = {ledger.DETAIL: "تفصيلي (الحسابات الفرعية)", 1: "المستوى 1 (التصنيف)", 2: "المستوى 2 (المجموعة)",
          3: "المستوى 3", 4: "المستوى 4"}


def render() -> None:
    guard(SCREEN)
    page_header("ميزان المراجعة", "Trial Balance بالأرصدة والمجاميع — من القيود المعتمدة فقط")
    d1, d2 = date_range("tb")
    c1, c2 = st.columns([2, 1])
    level = c1.selectbox("المستوى", list(LEVELS), format_func=LEVELS.get, key="tb_level")
    hide_zero = c2.checkbox("إخفاء الحسابات الصفرية", value=True, key="tb_zero")
    df = run_action(ledger.trial_balance, d1, d2, level, hide_zero)
    if df is None:
        return
    if df.empty:
        st.info("لا توجد قيود معتمدة في الفترة")
        return
    totals = ledger.tb_totals(df)
    view = df.drop(columns=["account_id"])
    money = {c: st.column_config.NumberColumn(format="%.2f") for c in view.columns[2:]}
    st.dataframe(view, hide_index=True, width="stretch", column_config=money)
    st.dataframe(pd.DataFrame([{"البيان": "الإجمالي"} | totals]), hide_index=True, width="stretch",
                 column_config=money)
    if ledger.is_balanced(totals):
        st.success("الميزان متزن ✓")
    else:
        st.error("الميزان غير متزن — راجع مدير النظام")
    st.caption("الأرصدة الافتتاحية تراكمية من أول قيد. إقفال حسابات النتيجة في الأرباح المرحلة هيتم مع القوائم المالية.")
    export_buttons(view, "ميزان المراجعة", SCREEN, "trial_balance",
                   subtitle=f"من {d1} إلى {d2} — {LEVELS[level]}", totals=totals)
