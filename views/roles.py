"""Roles and the per-screen permission matrix."""
import streamlit as st

from core.models.security import ACTION_LABELS, ACTIONS
from core.services import users as svc
from ui import permission_matrix as pm
from ui.common import guard, page_header, run_action

SCREEN = "roles"


def _state_key(role_id: int) -> str:
    return f"perm_df_{role_id}"


def _load(role_id: int, force: bool = False):
    key = _state_key(role_id)
    if force or key not in st.session_state:
        st.session_state[key] = pm.normalize(svc.permission_matrix(role_id))
        st.session_state[f"{key}_ver"] = st.session_state.get(f"{key}_ver", 0) + 1
    return st.session_state[key]


def _bump(role_id: int) -> None:
    key = f"{_state_key(role_id)}_ver"
    st.session_state[key] = st.session_state.get(key, 0) + 1


def _on_column_toggle(role_id: int, action: str) -> None:
    value = st.session_state[f"colall_{role_id}_{action}"]
    st.session_state[_state_key(role_id)] = pm.set_column(st.session_state[_state_key(role_id)], action, value)
    _bump(role_id)


def _on_editor_change(role_id: int, editor_key: str) -> None:
    edits = st.session_state[editor_key].get("edited_rows", {})
    st.session_state[_state_key(role_id)] = pm.apply_edits(st.session_state[_state_key(role_id)], edits)
    _bump(role_id)


def _matrix(user, role_id: int) -> None:
    df = _load(role_id)
    ver = st.session_state[f"{_state_key(role_id)}_ver"]
    can_edit = user.can(SCREEN, "edit")

    st.caption("علّم «تحديد الكل» فوق أي عمود علشان تديه لكل الشاشات، أو علّم «الكل» في أي صف علشان تدي الشاشة كل الصلاحيات.")
    cols = st.columns(len(ACTIONS))
    for col, action in zip(cols, ACTIONS):
        # The widget key includes the version so it re-reads its value after any change.
        st.session_state[f"colall_{role_id}_{action}"] = pm.column_all(df, action)
        col.checkbox(
            f"كل «{ACTION_LABELS[action]}»", key=f"colall_{role_id}_{action}", disabled=not can_edit,
            on_change=_on_column_toggle, args=(role_id, action),
        )

    editor_key = f"perm_editor_{role_id}_{ver}"
    view = df[["الوحدة", "الشاشة", pm.ROW_ALL, *ACTIONS, "screen_key"]]
    column_config = {
        "screen_key": None,
        "الوحدة": st.column_config.TextColumn(disabled=True),
        "الشاشة": st.column_config.TextColumn(disabled=True),
        pm.ROW_ALL: st.column_config.CheckboxColumn(pm.ROW_ALL),
    } | {a: st.column_config.CheckboxColumn(ACTION_LABELS[a]) for a in ACTIONS}
    st.data_editor(
        view, key=editor_key, hide_index=True, width="stretch", column_config=column_config,
        disabled=not can_edit, on_change=_on_editor_change, args=(role_id, editor_key),
    )
    st.caption("الصلاحيات اللي مالهاش معنى في شاشة معينة (زي «اعتماد» في شجرة الحسابات) بتتشال تلقائياً.")
    if can_edit:
        c1, c2 = st.columns([1, 4])
        if c1.button("💾 حفظ الصلاحيات", type="primary"):
            if run_action(svc.save_permission_matrix, user, role_id, st.session_state[_state_key(role_id)],
                          success="تم حفظ الصلاحيات"):
                _load(role_id, force=True)
                st.rerun()
        if c2.button("تراجع عن التعديلات"):
            _load(role_id, force=True)
            st.rerun()


def render() -> None:
    user = guard(SCREEN)
    page_header("الأدوار والصلاحيات")
    roles = svc.list_roles()

    if user.can(SCREEN, "add"):
        with st.expander("➕ دور جديد"):
            with st.form("role_add", clear_on_submit=True):
                name = st.text_input("اسم الدور", placeholder="مثال: محاسب، أمين مخزن، مدير إنتاج")
                desc = st.text_input("وصف (اختياري)")
                if st.form_submit_button("إضافة", type="primary"):
                    if run_action(svc.save_role, user, name, desc, success="تمت إضافة الدور"):
                        st.rerun()
    if not roles:
        st.info("لا توجد أدوار بعد. أضف دور ثم حدد صلاحياته.")
        return

    options = {r["id"]: f"{r['name']}  ({r['users']} مستخدم)" for r in roles}
    role_id = st.selectbox("الدور", list(options), format_func=options.get)
    role = next(r for r in roles if r["id"] == role_id)

    if user.can(SCREEN, "edit"):
        with st.expander("تعديل اسم الدور"):
            with st.form(f"role_edit_{role_id}"):
                name = st.text_input("اسم الدور", role["name"])
                desc = st.text_input("وصف", role["description"] or "")
                if st.form_submit_button("حفظ"):
                    if run_action(svc.save_role, user, name, desc, role_id, success="تم الحفظ"):
                        st.rerun()
    if user.can(SCREEN, "delete"):
        with st.expander("حذف الدور"):
            if st.button("تأكيد حذف الدور", key=f"role_del_{role_id}"):
                if run_action(svc.delete_role, user, role_id, success="تم حذف الدور"):
                    st.session_state.pop(_state_key(role_id), None)
                    st.rerun()

    st.subheader(f"صلاحيات: {role['name']}")
    _matrix(user, role_id)
