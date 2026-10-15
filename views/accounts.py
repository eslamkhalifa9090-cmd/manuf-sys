"""Chart of accounts screen."""
import streamlit as st
from sqlalchemy import select

from core.db import session_scope
from core.models import Account
from core.models.accounting import ACCOUNT_TYPES
from core.services import accounts as svc
from ui.common import export_buttons, guard, import_section, page_header, run_action

SCREEN = "accounts"


def _account_options(postable: bool | None = None, max_level: int | None = None) -> dict[int, str]:
    with session_scope() as s:
        q = select(Account).order_by(Account.code)
        rows = s.scalars(q).all()
        return {
            a.id: svc.tree_label(a.code, a.name_ar, a.level)
            for a in rows
            if (postable is None or a.is_postable == postable) and (max_level is None or a.level <= max_level)
        }


def _tree_tab(user) -> None:
    df = svc.accounts_frame()
    c1, c2, c3 = st.columns([2, 1, 1])
    query = c1.text_input("بحث بالكود أو الاسم", key="coa_q")
    type_filter = c2.selectbox("النوع", ["الكل"] + list(ACCOUNT_TYPES.values()), key="coa_type")
    show_inactive = c3.checkbox("إظهار الموقوفة", value=False, key="coa_inactive")
    if query:
        df = df[df["الكود"].str.contains(query) | df["اسم الحساب"].str.contains(query, case=False)]
    if type_filter != "الكل":
        df = df[df["النوع"] == type_filter]
    if not show_inactive:
        df = df[df["نشط"]]
    view = df.assign(**{"الحساب": [svc.tree_label(c, n, l) for c, n, l in zip(df["الكود"], df["اسم الحساب"], df["المستوى"])]})
    cols = ["الحساب", "الاسم بالإنجليزية", "النوع", "الطبيعة", "القائمة", "قسم قائمة الدخل",
            "حساب فرعي", "حساب مقابل", "نظامي", "نشط"]
    st.dataframe(view[cols], hide_index=True, width="stretch", height=560)
    st.caption("الحساب الفرعي = يقبل قيود. الحساب النظامي = تستخدمه القيود الآلية ولا يمكن حذفه أو إيقافه.")
    export_cols = ["الكود", "اسم الحساب", "الاسم بالإنجليزية", "المستوى", "النوع", "الطبيعة", "القائمة",
                   "قسم قائمة الدخل", "حساب فرعي", "نشط"]
    export_buttons(df[export_cols], "شجرة الحسابات", SCREEN, "chart_of_accounts")


def _add_tab(user) -> None:
    parents = _account_options(max_level=3)
    parent_id = st.selectbox("الحساب الأب", list(parents), format_func=parents.get, key="coa_parent")
    if parent_id is None:
        return
    suggested = run_action(svc.next_child_code, parent_id)
    with st.form("coa_add", clear_on_submit=True):
        code = st.text_input("الكود", value=suggested if isinstance(suggested, str) else "")
        name_ar = st.text_input("اسم الحساب")
        name_en = st.text_input("الاسم بالإنجليزية (اختياري)")
        if st.form_submit_button("إضافة الحساب", type="primary"):
            if run_action(svc.create_account, user, parent_id, name_ar, name_en, code, success="تمت إضافة الحساب"):
                st.rerun()


def _edit_tab(user) -> None:
    options = _account_options()
    account_id = st.selectbox("الحساب", list(options), format_func=options.get, key="coa_edit_sel")
    if account_id is None:
        return
    with session_scope() as s:
        acc = s.get(Account, account_id)
        name_ar, name_en, active, is_system, has_children = (
            acc.name_ar, acc.name_en or "", acc.is_active, acc.is_system, bool(acc.children)
        )
    with st.form(f"coa_edit_{account_id}"):
        new_ar = st.text_input("اسم الحساب", value=name_ar)
        new_en = st.text_input("الاسم بالإنجليزية", value=name_en)
        new_active = st.checkbox("نشط", value=active, disabled=is_system)
        if st.form_submit_button("حفظ التعديل", type="primary"):
            if run_action(svc.update_account, user, account_id, new_ar, new_en, new_active, success="تم الحفظ"):
                st.rerun()
    if user.can(SCREEN, "delete") and not is_system and not has_children:
        with st.expander("حذف الحساب"):
            st.warning("الحذف نهائي. الأفضل إيقاف الحساب لو اتسجل عليه حركات.")
            if st.button("تأكيد الحذف", key=f"coa_del_{account_id}"):
                if run_action(svc.delete_account, user, account_id, success="تم حذف الحساب"):
                    st.rerun()


def render() -> None:
    user = guard(SCREEN)
    page_header("شجرة الحسابات", "Chart of Accounts")
    tabs = ["الشجرة"]
    if user.can(SCREEN, "add"):
        tabs.append("إضافة حساب")
    if user.can(SCREEN, "edit"):
        tabs.append("تعديل / حذف")
    if user.can(SCREEN, "import"):
        tabs.append("استيراد من Excel")
    handlers = {
        "الشجرة": _tree_tab,
        "إضافة حساب": _add_tab,
        "تعديل / حذف": _edit_tab,
        "استيراد من Excel": lambda u: import_section(
            SCREEN, svc.import_template(), "شجرة الحسابات", svc.validate_import, svc.import_accounts,
            help_text="املأ القالب بكود الحساب واسمه وكود الحساب الأب. الحسابات الموجودة لن تتكرر.",
        ),
    }
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            handlers[name](user)
