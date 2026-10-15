"""The signed-in user's notifications: returned documents and reviewer notes."""
import streamlit as st

from core.services import notifications as svc
from ui.common import guard, page_header


def render() -> None:
    user = guard("notifications")
    page_header("الإشعارات والملاحظات", "ملاحظات المراجع على المستندات اللي سجلتها")
    unread_only = st.toggle("غير المقروء فقط", value=True)
    df = svc.list_for(user.id, unread_only)
    if df.empty:
        st.success("مفيش إشعارات جديدة")
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch")
    st.caption("عدّل المستند المرتجع من شاشته (قيود اليومية أو سندات القبض والصرف) وهيرجع تلقائياً للاعتماد.")
    if not df["مقروء"].all() and st.button("تعليم الكل كمقروء"):
        svc.mark_read(user.id, [int(i) for i in df["id"]])
        st.rerun()
