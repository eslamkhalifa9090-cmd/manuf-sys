"""Home: the month's figures, the year's trend and what needs attention. Each part shows only with its permission."""
from datetime import date

import streamlit as st

from core.models.documents import PENDING, RETURNED
from core.services import approval, molds, sales, statements, stock
from core.services.notifications import unread_count
from core.services.settings import get_settings
from ui.common import fiscal_year_bounds, guard, page_header

MONEY = st.column_config.NumberColumn(format="%.2f")


def _money(v: float) -> str:
    return f"{v:,.0f}"


def _finance(today: date) -> None:
    month_start = today.replace(day=1)
    pl = statements.income_statement([("m", month_start, today)], detail=False).set_index("البند")["m"]
    net_sales = float(pl.get("صافي المبيعات", 0) or 0)
    gross = float(pl.get("مجمل الربح", 0) or 0)
    bal = statements.balances_by_key(("ar", "ap"), today)
    cash = float(statements.cash_by_account(today)["الرصيد"].sum())
    stock_value = float(stock.balances_frame(today)["القيمة"].sum())
    k = st.columns(3)
    k[0].metric("مبيعات الشهر (صافي)", _money(net_sales))
    k[1].metric("مجمل ربح الشهر", _money(gross), f"{gross / net_sales * 100:.1f}%" if net_sales else None,
                delta_color="off")
    k[2].metric("النقدية والبنوك", _money(cash))
    k = st.columns(3)
    k[0].metric("مستحق على العملاء", _money(bal.get("ar", 0)))
    k[1].metric("مستحق للموردين", _money(-bal.get("ap", 0)))
    k[2].metric("قيمة المخزون", _money(stock_value))

    d1, d2 = fiscal_year_bounds(today)
    trend = statements.monthly_results(d1, min(d2, today))
    if trend.abs().to_numpy().sum():
        st.markdown("##### المبيعات والأرباح بالشهر (السنة المالية)")
        st.bar_chart(trend, stack=False, height=300, y_label="جنيه", color=["#1F4E79", "#4C9BE8", "#2E9E6A"])


def _sales(today: date) -> None:
    d1, _ = fiscal_year_bounds(today)
    year = sales.lines_report(d1, today)
    if year.empty:
        return
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### أكبر العملاء (السنة المالية)")
        top = (year.groupby("العميل", as_index=False)[["صافي المبيعات", "مجمل الربح"]].sum()
               .sort_values("صافي المبيعات", ascending=False).head(5))
        st.dataframe(top, hide_index=True, width="stretch",
                     column_config={"صافي المبيعات": MONEY, "مجمل الربح": MONEY})
    with c2:
        st.markdown("##### أكثر الأصناف مبيعاً (السنة المالية)")
        top = (year.groupby("الصنف", as_index=False)[["الكمية", "صافي المبيعات"]].sum()
               .sort_values("صافي المبيعات", ascending=False).head(5))
        st.dataframe(top, hide_index=True, width="stretch", column_config={"صافي المبيعات": MONEY})


def render() -> None:
    user = guard("dashboard")
    settings = get_settings()
    page_header(f"أهلاً {user.full_name}", settings.name_ar)
    today = date.today()

    alerts = st.columns(3)
    if user.can("approvals"):
        pending = approval.documents_frame((PENDING, RETURNED))
        alerts[0].metric("مستندات في انتظار الاعتماد", len(pending))
    alerts[1].metric("إشعارات غير مقروءة", unread_count(user.id))
    if user.can("mold_report"):
        report = molds.report_frame()
        warned = report[report["تنبيه"].astype(bool)] if len(report) else report
        alerts[2].metric("أسطمبات عليها تنبيه", len(warned))

    if user.can("income_statement"):
        st.divider()
        _finance(today)
    if user.can("sales_report"):
        st.divider()
        _sales(today)
    if user.can("mold_report") and len(warned):
        st.divider()
        st.markdown("##### تنبيهات الأسطمبات")
        st.dataframe(warned[["الكود", "الأسطمبة", "العميل", "% الاستهلاك", "تنبيه"]], hide_index=True, width="stretch")
    if user.can("approvals") and len(pending):
        st.divider()
        st.markdown("##### آخر المستندات في انتظار الاعتماد")
        st.dataframe(pending.drop(columns=["ref"]).tail(8), hide_index=True, width="stretch",
                     column_config={"القيمة": MONEY})
    if not user.can("income_statement"):
        st.caption("الأرقام المالية بتظهر هنا لمن عنده صلاحية عرض قائمة الدخل.")
