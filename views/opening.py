"""Opening balances at the cut-over date: one workbook import, or a manual journal and stock voucher."""
import streamlit as st

from core.models.documents import JOURNAL_OPENING
from core.models.inventory import STOCK_OPENING
from core.services import opening as svc
from core.services.journals import OPENING_SCREEN as SCREEN
from ui.common import fiscal_year_bounds, flash, guard, page_header, run_action
from views import manual_journals, stock_vouchers

MONEY = st.column_config.NumberColumn(format="%.2f")


def _status_style(row):
    bad = row.get("الحالة") != svc.READY
    return ["background-color: #FDE2E1" if bad else ""] * len(row)


def _shown(df):
    """As text, so empty cells stay blank (the grid prints them as "None"), right-to-left."""
    out = df.copy().astype(object)
    for c in out.columns:
        out[c] = [("" if v is None or v != v else
                   f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else
                   f"{v:%Y-%m-%d}" if hasattr(v, "strftime") else str(v)) for v in out[c]]
    return out[out.columns[::-1]]


def _import_tab(user) -> None:
    st.info("ملف واحد فيه كل الأرصدة في تاريخ بداية التشغيل: الحسابات، العملاء والموردين، المخزون، الأصول الثابتة "
            "والأسطمبات. العملاء والأصناف والأصول والأسطمبات اللي مش مسجلة بتتسجل، وبيتعمل قيد أرصدة افتتاحية "
            "وإذن مخزن افتتاحي في انتظار الاعتماد.")
    c1, c2 = st.columns([1, 3])
    as_of = c1.date_input("تاريخ بداية التشغيل", value=svc.day_before(fiscal_year_bounds()[0]), format="YYYY-MM-DD",
                          key="ob_date")
    memo = c2.text_input("البيان", value=f"أرصدة افتتاحية في {as_of}", key=f"ob_memo_{as_of}")
    st.download_button("⬇️ تحميل قالب الأرصدة الافتتاحية", data=svc.template(), file_name="opening_balances.xlsx",
                       key="ob_tpl")
    uploaded = st.file_uploader("ارفع الملف بعد ملئه", type=["xlsx", "xlsm"], key="ob_up")
    if uploaded is None:
        return
    frames = run_action(svc.read_workbook, uploaded.getvalue())
    if frames is None:
        return
    p = run_action(svc.plan, frames, as_of)
    if p is None:
        return
    k = st.columns(4)
    k[0].metric("مدين القيد", f"{p.debit:,.2f}")
    k[1].metric("دائن القيد", f"{p.credit:,.2f}")
    k[2].metric("قيمة المخزون", f"{p.stock_value:,.2f}")
    k[3].metric("رصيد 3303 بعد الاعتماد", f"{p.equity_difference:,.2f}",
                help="صفر لو الأرصدة كاملة ومتزنة. موجب = رصيد دائن، سالب = رصيد مدين.")
    for w in p.warnings:
        st.warning(w)
    for key, (title, _) in svc.SHEETS.items():
        df = p.checked[key]
        if df.empty:
            continue
        bad = int((df["الحالة"] != svc.READY).sum())
        with st.expander(f"{title}: {len(df)} سطر" + (f" — ❌ {bad} مرفوض" if bad else " — ✅"), expanded=bool(bad)):
            st.dataframe(_shown(df).style.apply(_status_style, axis=1), hide_index=True, width="stretch")
    if p.errors:
        st.error(f"فيه {p.errors} سطر مرفوض. صححهم في الملف وارفعه تاني.")
        return
    if st.button("تأكيد الاستيراد وإرسال المستندات للاعتماد", type="primary", key="ob_run"):
        res = run_action(svc.run, user, frames, as_of, memo)
        if res:
            c = res["created"]
            flash(f"تم: {c['parties']} عميل/مورد، {c['items']} صنف، {c['assets']} أصل، {c['molds']} أسطمبة جديدة. "
                  "قيد الأرصدة وإذن المخزن في انتظار الاعتماد.")
            st.session_state.pop("ob_up", None)
            st.rerun()


def _documents_tab(user) -> None:
    st.markdown("**قيود الأرصدة الافتتاحية**")
    manual_journals._list_tab(user, JOURNAL_OPENING, SCREEN, "ob_j")
    st.divider()
    st.markdown("**أذون المخزن الافتتاحية**")
    stock_vouchers._list_tab(user, STOCK_OPENING, SCREEN, "ob_s")


def _manual_journal_tab(user) -> None:
    st.info("لتعديل رصيد بعد الاستيراد، أو لإدخال الأرصدة يدوي. أرصدة العملاء والموردين بتتكتب على 1401 / 2101 "
            "مع اسم العميل أو المورد. الأصول والأسطمبات سجّل رصيدها الافتتاحي في سجلها كمان عشان المطابقة.")
    manual_journals._form(user, None, JOURNAL_OPENING, "ob_new_j")


def render() -> None:
    user = guard(SCREEN)
    page_header("الأرصدة الافتتاحية", "صلاحية منفصلة عن قيود اليومية · الفرق بيتقفل على 3303 ويرجع صفر لما الأرصدة "
                "تكمل")
    tabs = ["المستندات"]
    if user.can(SCREEN, "add") and user.can(SCREEN, "import"):
        tabs.insert(0, "استيراد من ملف")
    if user.can(SCREEN, "add"):
        tabs += ["قيد افتتاحي يدوي", "مخزون افتتاحي يدوي"]
    handlers = {
        "استيراد من ملف": _import_tab,
        "المستندات": _documents_tab,
        "قيد افتتاحي يدوي": _manual_journal_tab,
        "مخزون افتتاحي يدوي": lambda u: stock_vouchers._form(u, None, STOCK_OPENING, "ob_new_s"),
    }
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            handlers[name](user)
