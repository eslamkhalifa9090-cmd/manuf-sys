"""Login, first-run setup and forced password change."""
import time

import streamlit as st

from core.security import authenticate
from core.services import users as user_svc
from core.services.errors import BusinessError
from core.services.settings import get_settings
from ui.common import view_mode

MAX_ATTEMPTS = 5
LOCK_SECONDS = 60


def _logo() -> None:
    settings = get_settings()
    if settings.logo:
        st.image(settings.logo, width=140)
    st.markdown(f"## {settings.name_ar}")


def first_run_page() -> None:
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        _logo()
        st.subheader("إعداد النظام لأول مرة")
        st.info("أنشئ حساب الـ Master Admin. ده الحساب الوحيد اللي ليه كل الصلاحيات وإدارة النظام.")
        with st.form("first_run"):
            username = st.text_input("اسم المستخدم")
            full_name = st.text_input("الاسم بالكامل")
            pw1 = st.text_input("كلمة المرور", type="password")
            pw2 = st.text_input("تأكيد كلمة المرور", type="password")
            if st.form_submit_button("إنشاء الحساب", type="primary"):
                if pw1 != pw2:
                    st.error("كلمتا المرور غير متطابقتين")
                else:
                    try:
                        user_svc.create_first_master(username, full_name, pw1)
                    except BusinessError as exc:
                        st.error(str(exc))
                    else:
                        st.success("تم إنشاء الحساب، سجّل الدخول الآن")
                        time.sleep(1)
                        st.rerun()


def login_page() -> None:
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        _logo()
        st.subheader("تسجيل الدخول")
        locked_until = st.session_state.get("login_locked_until", 0)
        if time.time() < locked_until:
            st.error(f"محاولات كثيرة خاطئة. حاول بعد {int(locked_until - time.time())} ثانية.")
            return
        with st.form("login"):
            username = st.text_input("اسم المستخدم")
            password = st.text_input("كلمة المرور", type="password")
            modes = list(user_svc.VIEW_MODES)
            asked = st.query_params.get("view")
            mode = st.radio("طريقة العرض", modes, format_func=user_svc.VIEW_MODES.get, horizontal=True,
                            index=modes.index(asked) if asked in modes else None,
                            help="لو سبتها فاضية هيفتح بآخر طريقة اخترتها")
            if st.form_submit_button("دخول", type="primary"):
                user = authenticate(username, password)
                if user is None:
                    attempts = st.session_state.get("login_attempts", 0) + 1
                    st.session_state["login_attempts"] = attempts
                    if attempts >= MAX_ATTEMPTS:
                        st.session_state["login_locked_until"] = time.time() + LOCK_SECONDS
                        st.session_state["login_attempts"] = 0
                    st.error("اسم المستخدم أو كلمة المرور غير صحيحة")
                else:
                    st.session_state["login_attempts"] = 0
                    if mode:
                        user_svc.set_view_mode(user.id, mode)
                        user.view_mode = mode
                    set_view_mode(user.view_mode)
                    st.session_state["user"] = user
                    st.rerun()


def change_password_form(user, forced: bool = False) -> None:
    if forced:
        st.warning("لازم تغيّر كلمة المرور قبل ما تكمل.")
    with st.form("change_pw"):
        old = st.text_input("كلمة المرور الحالية", type="password")
        new1 = st.text_input("كلمة المرور الجديدة", type="password")
        new2 = st.text_input("تأكيد كلمة المرور الجديدة", type="password")
        if st.form_submit_button("حفظ", type="primary"):
            if new1 != new2:
                st.error("كلمتا المرور غير متطابقتين")
                return
            try:
                user_svc.change_own_password(user, old, new1)
            except BusinessError as exc:
                st.error(str(exc))
                return
            user.must_change_password = False
            st.success("تم تغيير كلمة المرور")
            time.sleep(1)
            st.rerun()


@st.dialog("تغيير كلمة المرور")
def change_password_dialog(user) -> None:
    change_password_form(user)


def set_view_mode(mode: str) -> None:
    """Apply a layout now and keep it in the link, so a phone's bookmark opens in the same layout."""
    st.session_state["view_mode"] = mode
    st.query_params["view"] = mode
