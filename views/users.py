"""User accounts."""
import streamlit as st

from core.db import session_scope
from core.models import User
from core.services import users as svc
from ui.common import export_buttons, guard, page_header, run_action

SCREEN = "users"


def _role_options() -> dict:
    return {None: "— بدون دور —"} | {r["id"]: r["name"] for r in svc.list_roles()}


def render() -> None:
    user = guard(SCREEN)
    page_header("المستخدمون")
    df = svc.list_users()
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch")
    export_buttons(df.drop(columns=["id"]), "المستخدمون", SCREEN, "users")
    roles = _role_options()

    if user.can(SCREEN, "add"):
        with st.expander("➕ إضافة مستخدم"):
            with st.form("user_add", clear_on_submit=True):
                c1, c2 = st.columns(2)
                username = c1.text_input("اسم المستخدم")
                full_name = c2.text_input("الاسم بالكامل")
                password = c1.text_input("كلمة مرور مؤقتة", type="password",
                                         help="المستخدم هيتطلب منه يغيرها أول ما يدخل")
                role_id = c2.selectbox("الدور", list(roles), format_func=roles.get)
                if st.form_submit_button("إضافة", type="primary"):
                    if run_action(svc.create_user, user, username, full_name, password, role_id,
                                  success="تمت إضافة المستخدم"):
                        st.rerun()

    if user.can(SCREEN, "edit") and len(df):
        with st.expander("✏️ تعديل مستخدم"):
            options = dict(zip(df["id"], df["اسم المستخدم"] + " - " + df["الاسم"]))
            target_id = st.selectbox("المستخدم", list(options), format_func=options.get, key="user_edit_sel")
            with session_scope() as s:
                target = s.get(User, target_id)
                full_name, role_id, active, is_master = target.full_name, target.role_id, target.is_active, target.is_master
            if is_master and not user.is_master:
                st.info("حساب الـ Master Admin لا يُعدل إلا بواسطته.")
            else:
                with st.form(f"user_edit_{target_id}"):
                    new_name = st.text_input("الاسم بالكامل", full_name)
                    if is_master:
                        st.write("الدور: Master Admin")
                        new_role = None
                    else:
                        keys = list(roles)
                        new_role = st.selectbox("الدور", keys, index=keys.index(role_id) if role_id in keys else 0,
                                                format_func=roles.get)
                    new_active = st.checkbox("نشط", active)
                    if st.form_submit_button("حفظ", type="primary"):
                        if run_action(svc.update_user, user, target_id, new_name, new_role, new_active,
                                      success="تم حفظ المستخدم"):
                            st.rerun()
                with st.form(f"user_pw_{target_id}", clear_on_submit=True):
                    pw = st.text_input("كلمة مرور جديدة", type="password")
                    if st.form_submit_button("تعيين كلمة المرور"):
                        if run_action(svc.reset_password, user, target_id, pw, success="تم تعيين كلمة المرور"):
                            st.rerun()
                if user.can(SCREEN, "delete") and not is_master and target_id != user.id:
                    if st.button("حذف المستخدم", key=f"user_del_{target_id}"):
                        if run_action(svc.delete_user, user, target_id, success="تم حذف المستخدم"):
                            st.rerun()
