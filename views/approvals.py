"""Approvals dashboard: approve, return with a note, or un-approve documents."""
import streamlit as st

from core.models.documents import APPROVED, PENDING, RETURNED
from core.services import approval
from ui.common import date_range, export_buttons, flash, guard, page_header, select_all_editor

SCREEN = approval.APPROVAL_SCREEN
MONEY = st.column_config.NumberColumn(format="%.2f")


def _optional_dates(key: str):
    with st.expander("تصفية بالتاريخ"):
        on = st.checkbox("تفعيل التصفية", key=f"{key}_on")
        d1, d2 = date_range(key)
    return (d1, d2) if on else (None, None)


def _table(df, key: str, hide_approver: bool) -> list[str]:
    view = df.drop(columns=["اعتمدها"]) if hide_approver else df
    return select_all_editor(view.set_index("ref"), key, column_config={"القيمة": MONEY})


def _preview(df, key: str) -> None:
    if df.empty:
        return
    labels = {r["ref"]: f"{r['رقم المستند']} - {r['نوع الحركة']} - {r['البيان']}" for _, r in df.iterrows()}
    with st.expander("معاينة القيد المحاسبي"):
        ref = st.selectbox("المستند", list(labels), format_func=labels.get, key=f"{key}_prev")
        doc_type, doc_id = approval.parse_ref(ref)
        lines = approval.preview(doc_type, doc_id)
        st.dataframe(lines, hide_index=True, width="stretch", column_config={"مدين": MONEY, "دائن": MONEY})
        st.caption(f"إجمالي المدين {lines['مدين'].sum():,.2f} — إجمالي الدائن {lines['دائن'].sum():,.2f}")


def _report(done: int, errors: list[str], verb: str) -> None:
    if done:
        flash(f"تم {verb} {done} مستند")
    for e in errors:
        st.error(e)
    if done and not errors:
        st.rerun()


def _pending_tab(user) -> None:
    d1, d2 = _optional_dates("ap_pending")
    df = approval.documents_frame((PENDING,), d1, d2)
    st.caption(f"عدد المستندات في انتظار الاعتماد: {len(df)} — إجمالي القيمة: {df['القيمة'].sum() if len(df) else 0:,.2f}")
    selected = _table(df, "ap_pending", hide_approver=True)
    _preview(df, "ap_pending")
    if not user.can(SCREEN, "approve"):
        st.info("عندك صلاحية العرض فقط")
        return
    c1, c2 = st.columns(2)
    with c1:
        if st.button(f"✅ اعتماد المحدد ({len(selected)})", type="primary", disabled=not selected, width="stretch"):
            done, errors = approval.run_many(approval.approve, user, [approval.parse_ref(r) for r in selected])
            _report(done, errors, "اعتماد")
    with c2:
        with st.popover(f"↩️ إرجاع للتعديل ({len(selected)})", disabled=not selected, width="stretch"):
            note = st.text_area("سبب الإرجاع (إجباري)", key="ap_return_note")
            if st.button("تأكيد الإرجاع", key="ap_return_go"):
                if not (note or "").strip():
                    st.error("لازم تكتب سبب الإرجاع")
                else:
                    done, errors = approval.run_many(approval.return_for_correction, user,
                                                     [approval.parse_ref(r) for r in selected], note)
                    _report(done, errors, "إرجاع")
    export_buttons(df.drop(columns=["ref"]), "مستندات في انتظار الاعتماد", SCREEN, "pending_documents")


def _returned_tab(user) -> None:
    df = approval.documents_frame((RETURNED,))
    if df.empty:
        st.info("لا توجد مستندات مرتجعة")
        return
    st.caption("المستندات دي رجعت لأصحابها للتعديل. بعد ما يعدلوها هترجع تاني لقائمة الانتظار.")
    st.dataframe(df.drop(columns=["ref", "اعتمدها"]), hide_index=True, width="stretch",
                 column_config={"القيمة": MONEY})


def _approved_tab(user) -> None:
    d1, d2 = _optional_dates("ap_done")
    df = approval.documents_frame((APPROVED,), d1, d2)
    selected = _table(df, "ap_done", hide_approver=False)
    _preview(df, "ap_done")
    if user.can(SCREEN, "unapprove"):
        with st.popover(f"🔓 إلغاء اعتماد المحدد ({len(selected)})", disabled=not selected):
            st.warning("إلغاء الاعتماد بيلغي القيد المحاسبي (يفضل محفوظ في السجل كملغي) ويرجع المستند للتعديل.")
            reason = st.text_area("السبب", key="ap_unapprove_reason")
            if st.button("تأكيد إلغاء الاعتماد", key="ap_unapprove_go"):
                done, errors = approval.run_many(approval.unapprove, user,
                                                 [approval.parse_ref(r) for r in selected], reason)
                _report(done, errors, "إلغاء اعتماد")
    export_buttons(df.drop(columns=["ref"]), "المستندات المعتمدة", SCREEN, "approved_documents")


def render() -> None:
    user = guard(SCREEN)
    page_header("الاعتمادات", "أي حركة مش بتأثر في الأرصدة أو التقارير إلا بعد اعتمادها من هنا")
    tabs = st.tabs(["في انتظار الاعتماد", "مرتجعة للتعديل", "المعتمدة"])
    with tabs[0]:
        _pending_tab(user)
    with tabs[1]:
        _returned_tab(user)
    with tabs[2]:
        _approved_tab(user)
