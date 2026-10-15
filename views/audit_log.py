import streamlit as st

from core.services import audit
from ui.common import export_buttons, guard, page_header

SCREEN = "audit_log"


def render() -> None:
    guard(SCREEN)
    page_header("سجل التدقيق", "كل عملية إضافة أو تعديل أو حذف بتتسجل هنا باسم المستخدم ووقتها")
    df = audit.list_log()
    c1, c2 = st.columns(2)
    users = ["الكل"] + sorted(x for x in df["المستخدم"].dropna().unique())
    entities = ["الكل"] + sorted(df["الكيان"].dropna().unique())
    u = c1.selectbox("المستخدم", users)
    e = c2.selectbox("الكيان", entities)
    if u != "الكل":
        df = df[df["المستخدم"] == u]
    if e != "الكل":
        df = df[df["الكيان"] == e]
    st.dataframe(df, hide_index=True, width="stretch", height=520)
    export_buttons(df, "سجل التدقيق", SCREEN, "audit_log")
