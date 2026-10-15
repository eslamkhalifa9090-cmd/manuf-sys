"""Shared Streamlit helpers: RTL styling, access guards, export and import widgets."""
from datetime import datetime

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
</style>
"""


def inject_css() -> None:
    st.markdown(RTL_CSS, unsafe_allow_html=True)


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
            data=lambda: to_excel(df, title, settings, subtitle=subtitle, printed_by=user.full_name),
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
