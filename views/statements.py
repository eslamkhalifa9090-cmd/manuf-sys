"""Financial statements, integrity checks and the year-end close."""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from core.models.documents import STATUS_LABELS
from core.services import approval, checks, fixed_assets as fa, statements as svc
from core.services.settings import get_settings
from ui.common import date_range, export_buttons, fiscal_year_bounds, guard, page_header, run_action

EDITABLE = (STATUS_LABELS["pending"], STATUS_LABELS["returned"])
MONEY = st.column_config.NumberColumn(format="%.2f")
STRONG = ("subtotal", "section", "check")


def _show(df: pd.DataFrame, title: str, screen: str, stem: str, subtitle: str) -> None:
    """A statement table with bold subtotals on screen and in the exports."""
    strong = {i for i, k in enumerate(df["kind"]) if k in STRONG}
    view = df.drop(columns=["kind"])
    values = [c for c in view.columns if c != "البند"]

    def style(r):
        css = "font-weight: bold; background-color: #DDEBF7" if df.loc[r.name, "kind"] in STRONG else ""
        return [css] * len(r)

    view[values] = view[values].astype(float)
    # Shown as text: the grid prints empty number cells as "None" whatever the formatting.
    shown = view.copy()
    shown[values] = view[values].map(lambda v: "" if pd.isna(v) else f"{v + 0.0:,.2f}")
    pct = df["kind"] == "pct"
    st.dataframe(shown.style.apply(style, axis=1).set_properties(subset=values, **{"text-align": "right"}),
                 hide_index=True, width="stretch", height=min(38 + 35 * len(view), 900))
    if pct.any():
        st.caption("صف «% مجمل الربح» نسبة مئوية، مش مبلغ.")
    export_buttons(view, title, screen, stem, subtitle=subtitle, bold_rows=strong)


def _year_options() -> tuple[list[int], int]:
    start_month = int(get_settings().fiscal_year_start_month or 1)
    current = fiscal_year_bounds()[0].year
    return list(range(current - 5, current + 2)), start_month


def _year_back(d: date) -> date:
    return d.replace(year=d.year - 1, day=28) if (d.month, d.day) == (2, 29) else d.replace(year=d.year - 1)


def _fy_label(y: int, m: int) -> str:
    d1, d2 = fa.fiscal_year(y, m)
    return f"{d1:%Y-%m} ← {d2:%Y-%m}"


# ---------------------------------------------------------------- income statement

def render_income_statement() -> None:
    guard("income_statement")
    page_header("قائمة الدخل", "من القيود المعتمدة؛ قيد إقفال السنة مستبعد عشان القائمة تفضل تظهر نتيجة السنة")
    mode = st.radio("العرض", ["monthly", "compare"], horizontal=True, key="is_mode",
                    format_func={"monthly": "شهري للسنة المالية", "compare": "مقارنة فترتين"}.get)
    detail = st.checkbox("تفصيلي بالحسابات", value=True, key="is_detail")
    if mode == "monthly":
        years, m = _year_options()
        y = st.selectbox("السنة المالية", years, index=years.index(fiscal_year_bounds()[0].year),
                         format_func=lambda v: _fy_label(v, m), key="is_year")
        d1, d2 = fa.fiscal_year(y, m)
        cols = svc.fiscal_months(d1, d2) + [("إجمالي السنة", d1, d2)]
        subtitle = f"السنة المالية من {d1} إلى {d2}"
        df = run_action(svc.income_statement, cols, detail)
    else:
        st.markdown("**الفترة الحالية**")
        a1, a2 = date_range("is_cur")
        st.markdown("**فترة المقارنة**")
        b1, b2 = date_range("is_prev", (_year_back(a1), _year_back(a2)))
        cur, prev = f"{a1} ← {a2}", f"{b1} ← {b2}"
        subtitle = f"الحالية {cur} · المقارنة {prev}"
        df = run_action(svc.income_statement, [(cur, a1, a2), (prev, b1, b2)], detail)
        if df is not None:
            df["التغير"] = [None if k == "pct" else (c or 0) - (p or 0) for k, c, p in zip(df["kind"], df[cur], df[prev])]
            df["% التغير"] = [round((c - p) / abs(p) * 100, 1) if k != "pct" and p else None
                              for k, c, p in zip(df["kind"], df[cur].fillna(0), df[prev].fillna(0))]
    if df is None:
        return
    _show(df, "قائمة الدخل", "income_statement", "income_statement", subtitle)


# ---------------------------------------------------------------- balance sheet

def render_balance_sheet() -> None:
    guard("balance_sheet")
    page_header("قائمة المركز المالي", "الأرصدة في تاريخين للمقارنة؛ ربح السنة اللي لسه ما اتقفلش بيظهر تحت حقوق الملكية")
    start, end = fiscal_year_bounds()
    c1, c2, c3 = st.columns([1, 1, 1])
    d1 = c1.date_input("في تاريخ", value=min(date.today(), end), format="YYYY-MM-DD", key="bs_d1")
    d2 = c2.date_input("مقارنة بتاريخ", value=start - timedelta(days=1), format="YYYY-MM-DD", key="bs_d2")
    detail = c3.checkbox("تفصيلي بالحسابات", value=False, key="bs_detail")
    df = run_action(svc.balance_sheet, [d1, d2], detail)
    if df is None:
        return
    diff = df.loc[df["kind"] == "check", d1.isoformat()].iloc[0]
    if abs(diff or 0) >= 0.01:
        st.error(f"المركز المالي مش متوازن بفرق {diff:,.2f}؛ افتح شاشة فحص سلامة البيانات")
    _show(df, "قائمة المركز المالي", "balance_sheet", "balance_sheet", f"في {d1} مقارنة بـ {d2}")


# ---------------------------------------------------------------- cash flow

def render_cash_flow() -> None:
    guard("cash_flow")
    page_header("قائمة التدفقات النقدية", "الطريقة المباشرة: كل حركة على الخزينة والبنوك (15xx) متصنفة بالحساب "
                "المقابل؛ التحويل بين حسابات النقدية مش تدفق")
    d1, d2 = date_range("cf")
    res = run_action(svc.cash_flow, d1, d2)
    if res is None:
        return
    df, summary = res
    k = st.columns(3)
    k[0].metric("النقدية أول الفترة", f"{summary['opening']:,.2f}")
    k[1].metric("صافي التغير", f"{summary['change']:,.2f}")
    k[2].metric("النقدية آخر الفترة", f"{summary['closing']:,.2f}")
    if not summary["matches"]:
        st.error("صافي التدفقات مش مطابق لرصيد النقدية؛ افتح شاشة فحص سلامة البيانات")
    _show(df, "قائمة التدفقات النقدية", "cash_flow", "cash_flow", f"من {d1} إلى {d2}")
    with st.expander("أرصدة حسابات النقدية آخر الفترة"):
        st.dataframe(svc.cash_by_account(d2), hide_index=True, width="stretch", column_config={"الرصيد": MONEY})


# ---------------------------------------------------------------- integrity checks

def render_checks() -> None:
    guard("integrity_checks")
    page_header("فحص سلامة البيانات", "مطابقة الأستاذ العام مع السجلات الفرعية وإقفالات الشهر والسنة")
    as_of = st.date_input("في تاريخ", value=date.today(), format="YYYY-MM-DD", key="ic_date")
    results = checks.run_all(as_of)
    counts = {s: sum(1 for c in results if c.status == s) for s in (checks.OK, checks.WARN, checks.ERROR)}
    k = st.columns(3)
    k[0].metric("✅ سليم", counts[checks.OK])
    k[1].metric("⚠️ يحتاج مراجعة", counts[checks.WARN])
    k[2].metric("❌ خطأ", counts[checks.ERROR])
    order = {checks.ERROR: 0, checks.WARN: 1, checks.OK: 2}
    for c in sorted(results, key=lambda c: order[c.status]):
        with st.expander(f"{checks.ICONS[c.status]} {c.title} — {c.result}", expanded=c.status == checks.ERROR):
            if c.detail.empty:
                st.caption("مفيش تفاصيل")
            else:
                money = {col: MONEY for col in c.detail.columns if pd.api.types.is_float_dtype(c.detail[col])}
                st.dataframe(c.detail, hide_index=True, width="stretch", column_config=money)


# ---------------------------------------------------------------- year-end close

def render_year_close() -> None:
    user = guard(fa.CLOSE_SCREEN)
    page_header("إقفال السنة المالية", "حسابات الإيرادات والمصروفات بتتقفل في الأرباح المرحلة 3301 بقيد واحد "
                "بيتعتمد زي أي مستند")
    if user.can(fa.CLOSE_SCREEN, "add"):
        with st.container(border=True):
            years, m = _year_options()
            last = fiscal_year_bounds()[0].year - 1
            y = st.selectbox("السنة المالية", years, index=years.index(last), format_func=lambda v: _fy_label(v, m),
                             key="yc_year")
            pv = fa.close_preview(y, m)
            k = st.columns(2)
            k[0].metric("صافي الربح (الخسارة) اللي هيتقفل", f"{pv['profit']:,.2f}")
            k[1].metric("مستندات لسه ما اتعتمدتش في السنة", pv["pending"])
            if pv["pending"]:
                st.warning("فيه مستندات في السنة لسه ما اتعتمدتش؛ اعتمدها أو احذفها قبل الإقفال عشان الرقم يبقى نهائي")
            if not pv["lines"].empty:
                with st.expander("أرصدة حسابات النتيجة"):
                    st.dataframe(pv["lines"], hide_index=True, width="stretch", column_config={"الرصيد": MONEY})
            st.caption("بعد الاعتماد اقفل شهور السنة من شاشة الفترات المحاسبية. لو فيه قيد اتسجل بعد الإقفال "
                       "على السنة دي، الغي اعتماد الإقفال واحسبه تاني.")
            memo = st.text_input("البيان", key="yc_memo")
            if st.button("احسب وابعت للاعتماد", type="primary", key="yc_compute"):
                if run_action(fa.compute_close, user, y, m, memo, success="تم حساب الإقفال. في انتظار الاعتماد."):
                    st.rerun()
    df = fa.closes_frame(user)
    if df.empty:
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch",
                 column_config={"صافي الربح المقفل": MONEY})
    export_buttons(df.drop(columns=["id"]), "إقفال السنوات المالية", fa.CLOSE_SCREEN, "year_close")
    labels = {int(r["id"]): f"{r['رقم المستند']} - {r['السنة المالية']} ({r['الحالة']})" for _, r in df.iterrows()}
    doc_id = st.selectbox("تفاصيل المستند", list(labels), format_func=labels.get, key="yc_pick")
    with st.expander("القيد"):
        st.dataframe(approval.preview(fa.CLOSE_SOURCE, doc_id), hide_index=True, width="stretch")
    if df.loc[df["id"] == doc_id, "الحالة"].iloc[0] in EDITABLE and user.can(fa.CLOSE_SCREEN, "delete"):
        if st.button("حذف المستند", key=f"yc_del_{doc_id}"):
            if run_action(fa.delete_close, user, doc_id, success="تم الحذف"):
                st.rerun()
