"""Account statement (general ledger card) with a running balance."""
import streamlit as st

from core.services import ledger
from ui.common import account_options, date_range, export_buttons, guard, page_header

SCREEN = "ledger"
MONEY = st.column_config.NumberColumn(format="%.2f")


def render() -> None:
    guard(SCREEN)
    page_header("كشف حساب", "حركة الحساب والرصيد التراكمي — الحساب الرئيسي بيجمع كل الحسابات اللي تحته")
    accounts = account_options(postable_only=False, include_inactive=True)
    ids = list(accounts)
    account_id = st.selectbox("الحساب", ids, format_func=accounts.get, index=None, placeholder="اختار الحساب",
                              key="gl_acc")
    d1, d2 = date_range("gl")
    if account_id is None:
        return
    df, info = ledger.account_statement(account_id, d1, d2)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("رصيد أول المدة", f"{info['opening']:,.2f}")
    c2.metric("مدين الفترة", f"{info['debit']:,.2f}")
    c3.metric("دائن الفترة", f"{info['credit']:,.2f}")
    c4.metric("الرصيد الختامي", f"{info['closing']:,.2f}")
    st.caption("الرصيد الموجب = مدين، والسالب = دائن.")
    view = df if info["is_header"] else df.drop(columns=["الحساب"])
    st.dataframe(view, hide_index=True, width="stretch",
                 column_config={"مدين": MONEY, "دائن": MONEY, "الرصيد": MONEY})
    export_buttons(view, f"كشف حساب {info['account']}", SCREEN, "account_statement",
                   subtitle=f"من {d1} إلى {d2}",
                   totals={"مدين": info["debit"], "دائن": info["credit"], "الرصيد": info["closing"]})
