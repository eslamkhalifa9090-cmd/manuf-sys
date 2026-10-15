"""Shared Streamlit helpers: RTL styling, access guards, export and import widgets."""
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from core.reports.exporters import to_excel, to_pdf
from core.security import CurrentUser
from core.services.errors import BusinessError
from core.services.settings import get_settings

RTL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700&display=swap');
html, body, .stApp, .stMarkdown, .stTextInput, .stSelectbox, .stButton, .stForm,
[data-testid="stSidebar"], [data-testid="stMetric"], .stAlert, .stTabs, .stRadio, .stCheckbox {
    font-family: 'Cairo', sans-serif;
}
.stApp { direction: rtl; }
.stApp p, .stApp li, .stApp h1, .stApp h2, .stApp h3, .stApp label, .stApp input, .stApp textarea { text-align: right; }
/* Data grids render on a canvas that only lays out left-to-right. */
[data-testid="stDataFrame"], [data-testid="stDataFrameResizable"], [data-testid="stDataEditor"] { direction: ltr; }
.stTabs [data-baseweb="tab-list"] { gap: 1.5rem; }
h1, h2, h3 { color: #1F4E79; }
[data-testid="stMetricValue"] { font-size: 1.6rem; }
/* In RTL the collapsed sidebar slides the wrong way and its icons bleed over the page. */
[data-testid="stSidebar"][data-collapsed="true"], [data-testid="stSidebar"][data-collapsed="true"] * {
    visibility: hidden !important;
}
</style>
"""


# Phone layout: figures two per row instead of one tall column, tighter headings, scrollable tabs.
_METRIC_ROW = '[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]):not(:has([data-testid="stHorizontalBlock"]))'
PHONE_RULES = f"""
{_METRIC_ROW} {{ flex-wrap: wrap; gap: .5rem; }}
{_METRIC_ROW} > [data-testid="stColumn"] {{
    min-width: calc(50% - .5rem) !important; flex: 1 1 calc(50% - .5rem) !important; width: calc(50% - .5rem) !important;
}}
[data-testid="stMetricValue"] {{ font-size: 1.15rem; }}
[data-testid="stMetricLabel"] p {{ font-size: .8rem; }}
.stApp h1 {{ font-size: 1.6rem; }}
.stTabs [data-baseweb="tab-list"] {{ gap: .75rem; overflow-x: auto; }}
.block-container {{ padding: 2.5rem 1rem 2rem 1rem; }}
"""
# Chosen "mobile" view: the phone rules at any width, inputs two per row, full-width buttons, a narrow page.
MOBILE_VIEW = """
.block-container { max-width: 760px; margin: 0 auto; }
[data-testid="stHorizontalBlock"]:not(:has([data-testid="stHorizontalBlock"])) { flex-wrap: wrap; gap: .5rem; }
[data-testid="stHorizontalBlock"]:not(:has([data-testid="stHorizontalBlock"])) > [data-testid="stColumn"] {
    min-width: calc(50% - .5rem) !important; flex: 1 1 calc(50% - .5rem) !important;
}
.stButton button, .stDownloadButton button, .stFormSubmitButton button { width: 100%; }
"""


def view_mode() -> str:
    """desktop or mobile: the signed-in choice, else the link's ?view=, else desktop."""
    mode = st.session_state.get("view_mode") or st.query_params.get("view") or "desktop"
    return mode if mode in ("desktop", "mobile") else "desktop"


# Phones: close the sidebar after a page is picked, so the page is not hidden behind the menu.
CLOSE_MENU_JS = """<script>
const d = window.parent.document;
if (!d._closeMenuOnNav) {
  d._closeMenuOnNav = true;
  d.addEventListener("click", (e) => {
    if (e.target.closest('[data-testid="stSidebarNav"] a') && window.parent.innerWidth < 768) {
      setTimeout(() => d.querySelector('[data-testid="stSidebarCollapseButton"] button')?.click(), 250);
    }
  }, true);
}
</script>"""


def inject_css() -> None:
    css = RTL_CSS.replace("</style>", f"@media (max-width: 640px) {{{PHONE_RULES}}}\n</style>")
    if view_mode() == "mobile":
        css = css.replace("</style>", PHONE_RULES + MOBILE_VIEW + "</style>")
    st.markdown(css, unsafe_allow_html=True)
    st.html(CLOSE_MENU_JS, unsafe_allow_javascript=True)


def current_user() -> CurrentUser:
    return st.session_state["user"]


def guard(screen_key: str, action: str = "view") -> CurrentUser:
    """Stop rendering unless the signed-in user holds the permission."""
    user = st.session_state.get("user")
    if user is None or not user.can(screen_key, action):
        st.error("ليس لديك صلاحية لهذه الشاشة")
        st.stop()
    return user


def page_header(title: str, caption: str | None = None) -> None:
    st.title(title)
    if caption:
        st.caption(caption)


def flash(message: str, kind: str = "success") -> None:
    """Queue a message that survives the next rerun."""
    st.session_state.setdefault("_flash", []).append((kind, message))


def show_flash() -> None:
    for kind, message in st.session_state.pop("_flash", []):
        st.toast(message, icon="✅" if kind == "success" else "⚠️")


def run_action(fn, *args, success: str | None = None, **kwargs):
    """Call a service; show Arabic business errors instead of crashing. Returns the result or None."""
    try:
        result = fn(*args, **kwargs)
    except BusinessError as exc:
        st.error(str(exc))
        return None
    if success:
        flash(success)
    return result if result is not None else True


def export_buttons(df: pd.DataFrame, title: str, screen_key: str, file_stem: str,
                   subtitle: str | None = None, totals: dict | None = None) -> None:
    """Excel and PDF download buttons, shown only with the export permission."""
    user = current_user()
    if not user.can(screen_key, "export"):
        return
    settings = get_settings()
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    c1, c2, _ = st.columns([1, 1, 4])
    with c1:
        st.download_button(
            "⬇️ Excel",
            data=lambda: to_excel(df, title, settings, subtitle=subtitle, printed_by=user.full_name, totals=totals),
            file_name=f"{file_stem}_{stamp}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"xl_{screen_key}_{file_stem}",
            width="stretch",
        )
    with c2:
        st.download_button(
            "⬇️ PDF",
            data=lambda: to_pdf(df, title, settings, subtitle=subtitle, printed_by=user.full_name, totals=totals),
            file_name=f"{file_stem}_{stamp}.pdf",
            mime="application/pdf",
            key=f"pdf_{screen_key}_{file_stem}",
            width="stretch",
        )


def import_section(screen_key: str, template: pd.DataFrame, template_name: str,
                   validate_fn, import_fn, help_text: str | None = None) -> None:
    """Template download, upload, validation preview and confirm, for any Excel import."""
    user = guard(screen_key, "import")
    settings = get_settings()
    if help_text:
        st.info(help_text)
    st.download_button(
        "⬇️ تحميل قالب الاستيراد",
        data=to_excel(template, f"قالب استيراد {template_name}", settings, sheet_name="بيانات",
                      include_footer=False),
        file_name=f"template_{screen_key}.xlsx",
        key=f"tpl_{screen_key}",
    )
    uploaded = st.file_uploader("ارفع ملف Excel بعد ملئه", type=["xlsx"], key=f"up_{screen_key}")
    if uploaded is None:
        return
    try:
        raw = _read_template(uploaded, list(template.columns))
        checked = validate_fn(raw)
    except BusinessError as exc:
        st.error(str(exc))
        return
    except Exception:
        st.error("تعذر قراءة الملف. استخدم القالب الذي تم تحميله من البرنامج.")
        return
    ready = int((checked["الحالة"] == "جاهز").sum())
    st.write(f"عدد الصفوف: {len(checked)} — جاهز للاستيراد: {ready} — مرفوض: {len(checked) - ready}")
    st.dataframe(checked, width="stretch", hide_index=True)
    if ready and st.button(f"تأكيد استيراد {ready} صف", type="primary", key=f"imp_{screen_key}"):
        count = run_action(import_fn, user, raw)
        if count:
            flash(f"تم استيراد {count} صف")
            st.rerun()


def account_options(postable_only: bool = True, include_inactive: bool = False) -> dict[int, str]:
    """{account id: "code - name"} in code order, for select boxes."""
    from sqlalchemy import select

    from core.db import session_scope
    from core.models import Account

    with session_scope() as s:
        q = select(Account).order_by(Account.code)
        if postable_only:
            q = q.where(Account.is_postable)
        if not include_inactive:
            q = q.where(Account.is_active)
        return {a.id: f"{a.code} - {a.name_ar}" for a in s.scalars(q)}


def fiscal_year_bounds(today: date | None = None) -> tuple[date, date]:
    """Start and end of the fiscal year containing `today`, per the company settings."""
    today = today or date.today()
    m = get_settings().fiscal_year_start_month or 1
    start_year = today.year if today.month >= m else today.year - 1
    start = date(start_year, m, 1)
    end = date(start_year + 1, m, 1) if m > 1 else date(start_year + 1, 1, 1)
    return start, end - timedelta(days=1)


def date_range(key: str, default: tuple[date, date] | None = None) -> tuple[date, date]:
    start, end = default or fiscal_year_bounds()
    c1, c2 = st.columns(2)
    d1 = c1.date_input("من تاريخ", value=start, key=f"{key}_from", format="YYYY-MM-DD")
    d2 = c2.date_input("إلى تاريخ", value=end, key=f"{key}_to", format="YYYY-MM-DD")
    return d1, d2


def select_all_editor(df: pd.DataFrame, key: str, height: int | str = "auto",
                      column_config: dict | None = None) -> list:
    """A read-only table with a tick column and a 'select all' box. Returns the selected index labels."""
    if df.empty:
        st.info("لا توجد بيانات")
        return []
    all_on = st.checkbox("تحديد الكل", key=f"{key}_all")
    view = df.copy()
    view.insert(0, "تحديد", all_on)
    # The editor key changes with the select-all box so ticking it resets every row.
    edited = st.data_editor(
        view, key=f"{key}_ed_{int(all_on)}", hide_index=True, width="stretch", height=height,
        disabled=[c for c in view.columns if c != "تحديد"], column_config=column_config,
    )
    return list(edited.index[edited["تحديد"]])


def _read_template(uploaded, columns: list[str]) -> pd.DataFrame:
    """Find the header row (templates carry a title block above the table)."""
    probe = pd.read_excel(uploaded, header=None, dtype=str)
    for i in range(min(len(probe), 15)):
        values = [str(v).strip() for v in probe.iloc[i].tolist()]
        if columns[0] in values:
            df = probe.iloc[i + 1:].copy()
            df.columns = values
            df = df.dropna(how="all")
            # Drop footer lines written under the table.
            return df[df[columns[0]].notna()].reset_index(drop=True)
    raise BusinessError("لم يتم العثور على صف العناوين: " + "، ".join(columns))
