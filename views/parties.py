"""Customers and suppliers."""
import streamlit as st

from core.models.parties import CUSTOMER, PARTY_KINDS, SUPPLIER
from core.services import parties as svc
from ui.common import export_buttons, guard, import_section, page_header, run_action

SCREEN = svc.SCREEN


def _list_tab(user) -> None:
    kinds = {"الكل": None, "العملاء": CUSTOMER, "الموردين": SUPPLIER}
    c1, c2 = st.columns([2, 2])
    kind = kinds[c1.radio("عرض", list(kinds), horizontal=True, key="pt_kind")]
    query = c2.text_input("بحث بالاسم أو الكود أو التليفون", key="pt_q")
    df = svc.parties_frame(kind)
    if query:
        df = df[df[["الكود", "الاسم", "التليفون"]].apply(lambda c: c.str.contains(query, case=False, regex=False)).any(axis=1)]
    if df.empty:
        st.info("لا توجد بيانات")
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch",
                 column_config={"الرصيد": st.column_config.NumberColumn(format="%.2f")})
    st.caption("الرصيد الموجب = مدين (على العميل)، والسالب = دائن (للمورد).")
    export_buttons(df.drop(columns=["id"]), "العملاء والموردين", SCREEN, "parties")


def _form(user, party: dict | None, key: str) -> None:
    p = party or {}
    kinds = list(PARTY_KINDS)
    with st.form(key, clear_on_submit=party is None):
        c1, c2 = st.columns([2, 1])
        name = c1.text_input("الاسم", value=p.get("الاسم", ""))
        kind = c2.selectbox("النوع", kinds, format_func=PARTY_KINDS.get,
                            index=kinds.index(p["kind"]) if p.get("kind") in kinds else 0)
        c3, c4 = st.columns(2)
        phone = c3.text_input("التليفون", value=p.get("التليفون", ""))
        address = c4.text_input("العنوان", value=p.get("العنوان", ""))
        notes = st.text_input("ملاحظات", value=p.get("ملاحظات", ""))
        active = st.checkbox("نشط", value=p.get("نشط", True)) if party else True
        if st.form_submit_button("حفظ", type="primary"):
            if run_action(svc.save_party, user, p.get("id"), name, kind, phone, address, notes, active, success="تم الحفظ"):
                st.rerun()


def _edit_tab(user) -> None:
    df = svc.parties_frame()
    if df.empty:
        st.info("لا توجد بيانات")
        return
    labels = {int(r["id"]): f"{r['الكود']} - {r['الاسم']} ({r['النوع']})" for _, r in df.iterrows()}
    pid = st.selectbox("اختار", list(labels), format_func=labels.get, key="pt_pick")
    row = df[df["id"] == pid].iloc[0].to_dict()
    row["kind"] = svc.KIND_BY_LABEL[row["النوع"]]
    _form(user, row, f"pt_edit_{pid}")
    if user.can(SCREEN, "delete"):
        with st.expander("حذف"):
            if st.button("تأكيد الحذف", key=f"pt_del_{pid}"):
                if run_action(svc.delete_party, user, pid, success="تم الحذف"):
                    st.rerun()


def render() -> None:
    user = guard(SCREEN)
    page_header("العملاء والموردين", "كل عميل أو مورد له كارت، وأرصدتهم على حسابي 1401 و 2101 كدفتر أستاذ مساعد")
    tabs = ["القائمة"] + (["إضافة"] if user.can(SCREEN, "add") else []) + \
        (["تعديل / حذف"] if user.can(SCREEN, "edit") else []) + (["استيراد من Excel"] if user.can(SCREEN, "import") else [])
    handlers = {
        "القائمة": _list_tab,
        "إضافة": lambda u: _form(u, None, "pt_new"),
        "تعديل / حذف": _edit_tab,
        "استيراد من Excel": lambda u: import_section(
            SCREEN, svc.import_template(), "العملاء والموردين", svc.validate_import, svc.import_parties,
            help_text="النوع: عميل أو مورد أو «عميل ومورد». الأرصدة الافتتاحية بتتسجل بعد كده بقيد أرصدة افتتاحية "
                      "فيه عمود «العميل / المورد».",
        ),
    }
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            handlers[name](user)
