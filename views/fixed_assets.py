"""Fixed-asset register and monthly straight-line depreciation."""
from datetime import date

import streamlit as st

from core.models.assets import ASSET_ACTIVE, ASSET_DEPARTMENTS, ASSET_DISPOSED, ASSET_STATUSES, FACTORY
from core.models.documents import STATUS_LABELS
from core.services import approval, fixed_assets as svc
from ui.common import export_buttons, guard, page_header, run_action

EDITABLE = (STATUS_LABELS["pending"], STATUS_LABELS["returned"])
MONEY = st.column_config.NumberColumn(format="%.2f")


def _clear(prefix: str) -> None:
    for k in [k for k in st.session_state if str(k).startswith(prefix)]:
        del st.session_state[k]


def _asset_form(user, a: dict | None, key: str) -> None:
    v = a or {}
    cats = svc.category_options()
    c1, c2, c3 = st.columns([2, 1, 1])
    name = c1.text_input("اسم الأصل", value=v.get("name", ""), key=f"{key}_name")
    code = c2.text_input("الكود (فاضي = تلقائي)", value=v.get("code", ""), key=f"{key}_code")
    ids = list(cats)
    account = c3.selectbox("الفئة", ids, format_func=cats.get, key=f"{key}_cat",
                           index=ids.index(v["account_id"]) if v.get("account_id") in ids else None,
                           placeholder="اختار الفئة")
    depts = list(ASSET_DEPARTMENTS)
    department = st.radio("الإهلاك على", depts, format_func=ASSET_DEPARTMENTS.get, horizontal=True,
                          index=depts.index(v.get("department", FACTORY)), key=f"{key}_dept")
    c4, c5, c6, c7 = st.columns(4)
    acquired = c4.date_input("تاريخ الشراء", value=v.get("acquisition_date"), format="YYYY-MM-DD", key=f"{key}_acq")
    start = c5.date_input("بداية الإهلاك", value=v.get("depreciation_start"), format="YYYY-MM-DD", key=f"{key}_start",
                          help="فاضي = شهر الشراء. الإهلاك بيتحسب شهر كامل من الشهر ده.")
    cost = c6.number_input("التكلفة", min_value=0.0, step=1000.0, value=float(v.get("cost") or 0), key=f"{key}_cost")
    salvage = c7.number_input("قيمة الخردة", min_value=0.0, step=100.0, value=float(v.get("salvage_value") or 0),
                              key=f"{key}_salv")
    c8, c9, c10 = st.columns(3)
    life = c8.number_input("العمر الإنتاجي (شهور)", min_value=0, step=12, value=int(v.get("life_months") or 60),
                           key=f"{key}_life")
    if life:
        c8.caption(f"إهلاك الشهر ≈ {(cost - salvage) / life:,.2f}")
    status = ASSET_ACTIVE
    disposal = None
    if a:
        sts = list(ASSET_STATUSES)
        status = c9.selectbox("الحالة", sts, format_func=ASSET_STATUSES.get, index=sts.index(v["status"]),
                              key=f"{key}_status")
        if status == ASSET_DISPOSED:
            disposal = c10.date_input("تاريخ الاستبعاد", value=v.get("disposal_date"), format="YYYY-MM-DD",
                                      key=f"{key}_disp")
            st.caption("الاستبعاد بيوقف الإهلاك من الشهر اللي بعده. قيد البيع أو الشطب (خروج التكلفة والمجمع) "
                       "بيتعمل بقيد يومية.")
    with st.expander("رصيد افتتاحي (لأصل موجود قبل بداية النظام)", expanded=bool(v.get("opening_date"))):
        st.caption("المجمع لحد تاريخ الرصيد؛ الإهلاك الشهري بيبدأ من الشهر اللي بعده. قيده في الأستاذ العام "
                   "بيتعمل من قيد الأرصدة الافتتاحية (11xx / 12xx).")
        o1, o2 = st.columns(2)
        opening_date = o1.date_input("تاريخ الرصيد", value=v.get("opening_date"), format="YYYY-MM-DD",
                                     key=f"{key}_odate")
        opening_acc = o2.number_input("مجمع الإهلاك في التاريخ ده", min_value=0.0, step=100.0,
                                      value=float(v.get("opening_accumulated") or 0), key=f"{key}_oacc")
    notes = st.text_input("ملاحظات", value=v.get("notes", ""), key=f"{key}_notes")
    st.caption("تكلفة الأصل بتدخل الدفاتر من فاتورة الشراء (سطر على حساب الفئة) أو من قيد الأرصدة الافتتاحية؛ "
               "السجل ده للإهلاك والمطابقة.")
    if st.button("حفظ", type="primary", key=f"{key}_save"):
        data = {"name": name, "code": code, "account_id": account, "department": department,
                "acquisition_date": acquired, "depreciation_start": start, "cost": cost, "salvage_value": salvage,
                "life_months": life, "opening_date": opening_date, "opening_accumulated": opening_acc,
                "status": status, "disposal_date": disposal, "notes": notes}
        if run_action(svc.save_asset, user, v.get("id"), data, success="تم حفظ الأصل"):
            if a is None:
                _clear(f"{key}_")
            st.rerun()


def _register_tab(user) -> None:
    as_of = st.date_input("الأرصدة في تاريخ", value=date.today(), format="YYYY-MM-DD", key="fa_asof")
    df = svc.register_frame(as_of)
    if df.empty:
        st.info("لسه مفيش أصول مسجلة")
        return
    money = {c: MONEY for c in ("التكلفة", "الخردة", "إهلاك الشهر", "مجمع الإهلاك", "صافي القيمة")}
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch", column_config=money)
    k = st.columns(3)
    k[0].metric("إجمالي التكلفة", f"{df['التكلفة'].sum():,.2f}")
    k[1].metric("مجمع الإهلاك", f"{df['مجمع الإهلاك'].sum():,.2f}")
    k[2].metric("صافي القيمة الدفترية", f"{df['صافي القيمة'].sum():,.2f}")
    export_buttons(df.drop(columns=["id"]), "سجل الأصول الثابتة", svc.SCREEN, "fixed_assets",
                   subtitle=f"في {as_of}")
    with st.expander("مطابقة السجل مع الأستاذ العام"):
        rec = svc.reconciliation(as_of)
        st.dataframe(rec, hide_index=True, width="stretch",
                     column_config={c: MONEY for c in rec.columns if c != "الفئة"})
        if len(rec) and (rec["فرق التكلفة"].abs().sum() or rec["فرق المجمع"].abs().sum()):
            st.warning("فيه فرق: غالباً أصل اتشترى ولسه ما اتسجلش هنا، أو إهلاك لسه ما اتعتمدش")
    if user.can(svc.SCREEN, "edit") or user.can(svc.SCREEN, "delete"):
        labels = {int(r["id"]): f"{r['الكود']} - {r['الأصل']}" for _, r in df.iterrows()}
        asset_id = st.selectbox("تعديل أصل", list(labels), format_func=labels.get, key="fa_pick")
        with st.expander("تعديل الأصل"):
            if user.can(svc.SCREEN, "edit"):
                _asset_form(user, svc.get_asset(asset_id), f"fa_edit_{asset_id}")
            if user.can(svc.SCREEN, "delete") and st.button("حذف الأصل", key=f"fa_del_{asset_id}"):
                if run_action(svc.delete_asset, user, asset_id, success="تم الحذف"):
                    st.rerun()


def render_register() -> None:
    user = guard(svc.SCREEN)
    page_header("سجل الأصول الثابتة", "قسط ثابت شهري = (التكلفة - الخردة) ÷ العمر بالشهور · المصنع على 5207 "
                "والإدارة على 6111 · الدائن مجمع الإهلاك 12xx")
    tabs = ["السجل"] + (["أصل جديد"] if user.can(svc.SCREEN, "add") else [])
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            _register_tab(user) if name == "السجل" else _asset_form(user, None, "fa_new")


def render_depreciation() -> None:
    user = guard(svc.DEP_SCREEN)
    page_header("الإهلاك الشهري", "مستند واحد لكل شهر بكل الأصول اللي في الخدمة؛ الشهور بتتحسب وتتعتمد بالترتيب")
    if user.can(svc.DEP_SCREEN, "add"):
        with st.container(border=True):
            today = date.today()
            prev = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
            c1, c2, _ = st.columns([1, 1, 3])
            year = c1.number_input("السنة", min_value=2000, max_value=2100, value=prev[0], step=1, key="dp_year")
            month = c2.number_input("الشهر", min_value=1, max_value=12, value=prev[1], step=1, key="dp_month")
            plan = svc.preview_month(int(year), int(month))
            if plan.empty:
                st.info("مفيش أصول عليها إهلاك في الشهر ده")
            else:
                st.dataframe(plan, hide_index=True, width="stretch",
                             column_config={"إهلاك الشهر": MONEY, "صافي القيمة قبل الشهر": MONEY})
                st.markdown(f"**إجمالي إهلاك الشهر: {plan['إهلاك الشهر'].sum():,.2f}**")
                memo = st.text_input("البيان", key="dp_memo")
                if st.button("احسب وابعت للاعتماد", type="primary", key="dp_compute"):
                    if run_action(svc.compute_month, user, int(year), int(month), memo,
                                  success="تم حساب الإهلاك. في انتظار الاعتماد."):
                        st.rerun()
    df = svc.depreciations_frame(user)
    if df.empty:
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch", column_config={"القيمة": MONEY})
    export_buttons(df.drop(columns=["id"]), "الإهلاك الشهري", svc.DEP_SCREEN, "depreciation")
    labels = {int(r["id"]): f"{r['رقم المستند']} - {r['الشهر']} ({r['الحالة']})" for _, r in df.iterrows()}
    doc_id = st.selectbox("تفاصيل المستند", list(labels), format_func=labels.get, key="dp_pick")
    st.dataframe(svc.depreciation_lines(doc_id), hide_index=True, width="stretch", column_config={"القيمة": MONEY})
    with st.expander("القيد"):
        st.dataframe(approval.preview("depreciation", doc_id), hide_index=True, width="stretch")
    if df.loc[df["id"] == doc_id, "الحالة"].iloc[0] in EDITABLE and user.can(svc.DEP_SCREEN, "delete"):
        if st.button("حذف المستند", key=f"dp_del_{doc_id}"):
            if run_action(svc.delete_depreciation, user, doc_id, success="تم الحذف"):
                st.rerun()
