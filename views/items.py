"""Items and item groups."""
import streamlit as st

from core.models.inventory import GROUP_KINDS
from core.services import items as svc
from ui.common import export_buttons, guard, import_section, page_header, run_action

SCREEN = svc.SCREEN


def _list_tab(user) -> None:
    df = svc.items_frame()
    groups = ["الكل"] + list(svc.group_options(include_inactive=True).values())
    c1, c2 = st.columns([1, 2])
    g = c1.selectbox("المجموعة", groups, key="it_group")
    query = c2.text_input("بحث بالكود أو الاسم أو البند", key="it_q")
    if g != "الكل":
        df = df[df["المجموعة"] == g]
    if query:
        df = df[df[["الكود", "اسم الصنف", "البند / العائلة"]].apply(
            lambda c: c.str.contains(query, case=False, regex=False)).any(axis=1)]
    if df.empty:
        st.info("لا توجد أصناف")
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch")
    st.caption(f"عدد الأصناف: {len(df)}")
    export_buttons(df.drop(columns=["id"]), "الأصناف", SCREEN, "items")


def _form(user, item: dict | None, key: str) -> None:
    groups = svc.group_options()
    v = item or {}
    gids = list(groups)
    with st.form(key, clear_on_submit=item is None):
        c1, c2 = st.columns([3, 1])
        name = c1.text_input("اسم الصنف", value=v.get("اسم الصنف", ""))
        code = c2.text_input("الكود (فاضي = تلقائي)", value=v.get("الكود", ""))
        c3, c4, c5 = st.columns(3)
        gname = v.get("المجموعة")
        gid = c3.selectbox("المجموعة", gids, format_func=groups.get,
                           index=[groups[i] for i in gids].index(gname) if gname in groups.values() else 0)
        units = svc.UNITS + ([v["الوحدة"]] if v.get("الوحدة") and v["الوحدة"] not in svc.UNITS else [])
        unit = c4.selectbox("الوحدة", units, index=units.index(v["الوحدة"]) if v.get("الوحدة") in units else 0,
                            accept_new_options=True)
        min_qty = c5.number_input("حد الطلب", min_value=0.0, value=float(v.get("حد الطلب") or 0))
        c6, c7, c8 = st.columns(3)
        ppu = c6.number_input("عدد القطع في الوحدة", min_value=0.001, value=float(v.get("قطع في الوحدة") or 1),
                              help="الجوز = 2. السعر بيظهر للقطعة وللوحدة وللكيلو")
        w = v.get("وزن القطعة (جم)")
        weight = c7.number_input("وزن القطعة بالجرام (اختياري)", min_value=0.0,
                                 value=float(w) if w is not None and w == w else 0.0,
                                 help="لو فاضي (صفر) بيتحسب من التركيبة أوتوماتيك")
        family = c8.text_input("البند / العائلة (للتجميع في التقارير)", value=v.get("البند / العائلة", ""))
        notes = st.text_input("ملاحظات", value=v.get("ملاحظات", ""))
        active = st.checkbox("نشط", value=v.get("نشط", True)) if item else True
        if st.form_submit_button("حفظ", type="primary"):
            if run_action(svc.save_item, user, v.get("id"), name, gid, unit, code, family, min_qty, notes, active,
                          ppu, weight or None, success="تم الحفظ"):
                st.rerun()


def _edit_tab(user) -> None:
    df = svc.items_frame()
    if df.empty:
        st.info("لا توجد أصناف")
        return
    labels = {int(r["id"]): f"{r['الكود']} - {r['اسم الصنف']}" for _, r in df.iterrows()}
    iid = st.selectbox("الصنف", list(labels), format_func=labels.get, key="it_pick")
    _form(user, df[df["id"] == iid].iloc[0].to_dict(), f"it_edit_{iid}")
    if user.can(SCREEN, "delete"):
        with st.expander("حذف الصنف"):
            if st.button("تأكيد الحذف", key=f"it_del_{iid}"):
                if run_action(svc.delete_item, user, iid, success="تم الحذف"):
                    st.rerun()


def _groups_tab(user) -> None:
    df = svc.groups_frame()
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch")
    st.caption("كل مجموعة مربوطة بحساب مخزون؛ قيود الأصناف بتروح للحساب ده تلقائياً.")
    if not user.can(SCREEN, "edit"):
        return
    accounts = svc.inventory_accounts()
    options = {None: "➕ مجموعة جديدة"} | {int(r["id"]): r["المجموعة"] for _, r in df.iterrows()}
    gid = st.selectbox("تعديل أو إضافة", list(options), format_func=options.get, key="ig_pick")
    row = df[df["id"] == gid].iloc[0].to_dict() if gid else {}
    kinds = list(GROUP_KINDS)
    acc_ids = list(accounts)
    with st.form(f"ig_form_{gid}"):
        name = st.text_input("اسم المجموعة", value=row.get("المجموعة", ""))
        kind_label = row.get("النوع")
        kind = st.selectbox("النوع", kinds, format_func=GROUP_KINDS.get,
                            index=[GROUP_KINDS[k] for k in kinds].index(kind_label) if kind_label in GROUP_KINDS.values() else 0)
        acc_label = row.get("حساب المخزون")
        acc_id = st.selectbox("حساب المخزون", acc_ids, format_func=accounts.get,
                              index=[accounts[a] for a in acc_ids].index(acc_label) if acc_label in accounts.values() else 0)
        active = st.checkbox("نشطة", value=row.get("نشطة", True))
        if st.form_submit_button("حفظ المجموعة", type="primary"):
            if run_action(svc.save_group, user, gid, name, kind, acc_id, active, success="تم الحفظ"):
                st.rerun()


def render() -> None:
    user = guard(SCREEN)
    page_header("الأصناف ومجموعاتها", "الصنف بيتسجل مرة واحدة من غير السعر في الكود؛ التكلفة بالمتوسط المرجح")
    tabs = ["الأصناف"] + (["إضافة صنف"] if user.can(SCREEN, "add") else []) + \
        (["تعديل / حذف"] if user.can(SCREEN, "edit") else []) + ["المجموعات"] + \
        (["استيراد من Excel"] if user.can(SCREEN, "import") else [])
    handlers = {
        "الأصناف": _list_tab,
        "إضافة صنف": lambda u: _form(u, None, "it_new"),
        "تعديل / حذف": _edit_tab,
        "المجموعات": _groups_tab,
        "استيراد من Excel": lambda u: import_section(
            SCREEN, svc.import_template(), "الأصناف", svc.validate_import, svc.import_items,
            help_text="اكتب اسم الصنف من غير السعر، واسم المجموعة زي ما هو في البرنامج. الكود لو فاضي بيتعمل تلقائي. "
                      "الأرصدة الافتتاحية بتتسجل بعد كده من شاشة أذون المخزن.",
        ),
    }
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            handlers[name](user)
