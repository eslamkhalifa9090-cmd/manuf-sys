"""Molds: register and card, usage counter, maintenance, monthly amortization, settlement, report."""
from datetime import date

import pandas as pd
import streamlit as st

from core.models.documents import STATUS_LABELS
from core.models.molds import (AGREED, AMORTIZED, COUNTER_UNITS, CUSTOMER_OWNED, FACTORY, MAINTENANCE_KINDS,
                               MOLD_STATUSES, OVERHAUL, OWNERSHIP)
from core.models.parties import CUSTOMER, SUPPLIER
from core.services import approval, cash, items, mold_docs as docs, molds as svc, parties
from ui.common import export_buttons, guard, page_header, run_action

EDITABLE = (STATUS_LABELS["pending"], STATUS_LABELS["returned"])
MONEY = st.column_config.NumberColumn(format="%.2f")
QTY = st.column_config.NumberColumn(format="%.0f")


def _clear(prefix: str) -> None:
    for k in [k for k in st.session_state if str(k).startswith(prefix)]:
        del st.session_state[k]


def _pick(label: str, opts: dict, current, key: str, container=st, placeholder: str = "اختار من القائمة"):
    ids = list(opts)
    return container.selectbox(label, ids, format_func=opts.get, index=ids.index(current) if current in ids else None,
                        placeholder=placeholder, key=key)


# ---------------------------------------------------------------- register

def _mold_form(user, m: dict | None, key: str) -> None:
    v = m or {}
    c1, c2, c3 = st.columns([2, 1, 1])
    name = c1.text_input("اسم الأسطمبة", value=v.get("name", ""), key=f"{key}_name")
    code = c2.text_input("الكود (فاضي = تلقائي)", value=v.get("code", ""), key=f"{key}_code")
    status = v.get("status")
    if m:
        sts = list(MOLD_STATUSES)
        status = c3.selectbox("الحالة", sts, format_func=MOLD_STATUSES.get, index=sts.index(v["status"]),
                              key=f"{key}_status")
    owns = list(OWNERSHIP)
    ownership = st.radio("الملكية", owns, format_func=OWNERSHIP.get, horizontal=True, key=f"{key}_own",
                         index=owns.index(v.get("ownership", FACTORY)))
    units = list(COUNTER_UNITS)
    c4, c5, c6, c7 = st.columns(4)
    unit = c4.radio("وحدة العداد", units, format_func=COUNTER_UNITS.get, horizontal=True, key=f"{key}_unit",
                    index=units.index(v.get("counter_unit", "piece")))
    cavities = c5.number_input("عدد التجاويف", min_value=1, step=1, value=int(v.get("cavities") or 1),
                               key=f"{key}_cav")
    life = salvage = 0.0
    if ownership in AMORTIZED:
        life = c6.number_input(f"العمر الافتراضي ({COUNTER_UNITS[unit]})", min_value=0.0, step=1000.0,
                               value=float(v.get("useful_life") or 0), key=f"{key}_life")
        salvage = c7.number_input("قيمة الخردة", min_value=0.0, step=100.0, value=float(v.get("salvage_value") or 0),
                                  key=f"{key}_salv")
    else:
        c6.caption("أسطمبة العميل: عداد بس، من غير تكلفة ولا إهلاك")
    c8, c9, c10 = st.columns(3)
    supplier = _pick("المورد (اللي صنعها)", parties.options(SUPPLIER), v.get("supplier_id"), f"{key}_sup", c8)
    purchase_date = c9.date_input("تاريخ الشراء", value=v.get("purchase_date"), format="YYYY-MM-DD",
                                  key=f"{key}_pdate")
    customer = agreed_qty = agreement_end = None
    if ownership != FACTORY:
        customer = _pick("العميل", parties.options(CUSTOMER), v.get("customer_id"), f"{key}_cus", c10)
    if ownership == AGREED:
        c11, c12 = st.columns(2)
        agreed_qty = c11.number_input(f"الحد الأدنى المتفق عليه ({COUNTER_UNITS[unit]})", min_value=0.0, step=1000.0,
                                      value=float(v.get("agreed_qty") or 0), key=f"{key}_agq")
        agreement_end = c12.date_input("تاريخ انتهاء الاتفاق", value=v.get("agreement_end"), format="YYYY-MM-DD",
                                       key=f"{key}_agend")
    c13, c14 = st.columns(2)
    products = c13.text_input("المنتجات اللي بتطلعها", value=v.get("products") or "", key=f"{key}_prod")
    notes = c14.text_input("ملاحظات", value=v.get("notes") or "", key=f"{key}_notes")
    opening = {"opening_date": v.get("opening_date"), "opening_cost": v.get("opening_cost", 0),
               "opening_amortization": v.get("opening_amortization", 0), "opening_usage": v.get("opening_usage", 0)}
    with st.expander("رصيد افتتاحي (لأسطمبة موجودة قبل بداية النظام)", expanded=bool(v.get("opening_date"))):
        st.caption("الأرقام دي للسجل بس؛ قيدها في الأستاذ العام بيتعمل من قيد الأرصدة الافتتاحية (1106 / 1206).")
        o1, o2, o3, o4 = st.columns(4)
        opening["opening_date"] = o1.date_input("تاريخ الرصيد", value=v.get("opening_date"), format="YYYY-MM-DD",
                                                key=f"{key}_odate")
        if ownership in AMORTIZED:
            opening["opening_cost"] = o2.number_input("التكلفة", min_value=0.0, step=100.0,
                                                      value=float(v.get("opening_cost") or 0), key=f"{key}_ocost")
            opening["opening_amortization"] = o3.number_input("مجمع الاستهلاك", min_value=0.0, step=100.0,
                                                              value=float(v.get("opening_amortization") or 0),
                                                              key=f"{key}_oacc")
        opening["opening_usage"] = o4.number_input(f"المستهلك قبل البداية ({COUNTER_UNITS[unit]})", min_value=0.0,
                                                   step=1000.0, value=float(v.get("opening_usage") or 0),
                                                   key=f"{key}_ouse")
    p1, p2 = st.columns([2, 1])
    photo = p1.file_uploader("صورة الأسطمبة (اختياري، حتى 2 ميجا)", type=["png", "jpg", "jpeg", "webp"],
                             key=f"{key}_img")
    clear_image = False
    if v.get("image"):
        p2.image(v["image"], width=140)
        clear_image = p2.checkbox("شيل الصورة", key=f"{key}_noimg")
    if st.button("حفظ", type="primary", key=f"{key}_save"):
        data = {"name": name, "code": code, "ownership": ownership, "counter_unit": unit, "cavities": cavities,
                "useful_life": life, "salvage_value": salvage, "supplier_id": supplier, "customer_id": customer,
                "purchase_date": purchase_date, "agreed_qty": agreed_qty, "agreement_end": agreement_end,
                "products": products, "notes": notes, "status": status, "clear_image": clear_image, **opening}
        if photo is not None:
            data["image"], data["image_mime"] = photo.getvalue(), photo.type
        if run_action(svc.save_mold, user, v.get("id"), data, success="تم حفظ الأسطمبة"):
            if m is None:
                _clear(f"{key}_")
            st.rerun()


def _mold_card(user, mold_id: int) -> None:
    m = svc.get_mold(mold_id)
    p = m["position"]
    unit = COUNTER_UNITS[m["counter_unit"]]
    c1, c2 = st.columns([1, 3])
    if m["image"]:
        c1.image(m["image"], width="stretch")
    c2.markdown(f"#### {m['code']} - {m['name']}")
    c2.caption(f"{OWNERSHIP[m['ownership']]} · {m['cavities']} تجويف · العداد بال{unit} · "
               f"{MOLD_STATUSES[m['status']]}"
               + (f" · العميل: {m['customer_name']}" if m["customer_name"] else "")
               + (f" · المورد: {m['supplier_name']}" if m["supplier_name"] else ""))
    k = c2.columns(4)
    if m["ownership"] in AMORTIZED:
        k[0].metric("التكلفة", f"{p['cost']:,.2f}")
        k[1].metric("مجمع الاستهلاك", f"{p['accumulated']:,.2f}")
        k[2].metric("صافي القيمة الدفترية", f"{p['book_value']:,.2f}")
        k[3].metric(f"إهلاك ال{unit} الحالي", f"{p['rate']:,.4f}")
    k = c2.columns(4)
    k[0].metric("العمر الافتراضي", f"{p['life']:,.0f}")
    k[1].metric("المستهلك", f"{p['used']:,.0f}", f"{p['used_pct']:g}%", delta_color="off")
    k[2].metric("الباقي", f"{p['remaining']:,.0f}")
    if m["ownership"] == AGREED:
        k[3].metric("المتفق عليه", f"{m['agreed_qty']:,.0f}",
                    f"لحد {m['agreement_end']}" if m["agreement_end"] else None, delta_color="off")
    t1, t2, t3, t4 = st.tabs(["التشغيل", "الصيانة والتعمير", "الإهلاك", "التسويات"])
    with t1:
        st.dataframe(svc.usage_frame(mold_id).drop(columns=["id", "الأسطمبة"]), hide_index=True, width="stretch")
    with t2:
        st.dataframe(docs.maintenance_frame(user, mold_id).drop(columns=["id", "الأسطمبة"]), hide_index=True,
                     width="stretch", column_config={"المبلغ": MONEY})
    with t3:
        st.dataframe(docs.mold_amortization_history(mold_id), hide_index=True, width="stretch",
                     column_config={"القيمة": MONEY})
    with t4:
        df = docs.settlements_frame(user)
        label = f"{m['code']} - {m['name']}"
        st.dataframe(df[df["الأسطمبة"] == label].drop(columns=["id", "الأسطمبة"]), hide_index=True, width="stretch")


def _register_tab(user) -> None:
    df = svc.report_frame()
    if df.empty:
        st.info("لسه مفيش أسطمبات مسجلة")
        return
    cols = ["الكود", "الأسطمبة", "الملكية", "العميل", "وحدة العداد", "التجاويف", "صافي القيمة الدفترية",
            "% الاستهلاك", "الحالة", "تنبيه"]
    st.dataframe(df[cols], hide_index=True, width="stretch", column_config={"صافي القيمة الدفترية": MONEY})
    export_buttons(df.drop(columns=["id"]), "سجل الأسطمبات", svc.SCREEN, "molds")
    st.divider()
    labels = {int(r["id"]): f"{r['الكود']} - {r['الأسطمبة']}" for _, r in df.iterrows()}
    mold_id = st.selectbox("كارت الأسطمبة", list(labels), format_func=labels.get, key="mold_card_pick")
    _mold_card(user, mold_id)
    if user.can(svc.SCREEN, "edit") or user.can(svc.SCREEN, "delete"):
        with st.expander("تعديل الأسطمبة"):
            if user.can(svc.SCREEN, "edit"):
                _mold_form(user, svc.get_mold(mold_id), f"mold_edit_{mold_id}")
            if user.can(svc.SCREEN, "delete") and st.button("حذف الأسطمبة", key=f"mold_del_{mold_id}"):
                if run_action(svc.delete_mold, user, mold_id, success="تم الحذف"):
                    st.rerun()


def render_register() -> None:
    user = guard(svc.SCREEN)
    page_header("سجل الأسطمبات", "الملكية، التجاويف، العمر الافتراضي، الأرصدة الافتتاحية وكارت كل أسطمبة")
    tabs = ["السجل"] + (["أسطمبة جديدة"] if user.can(svc.SCREEN, "add") else [])
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            _register_tab(user) if name == "السجل" else _mold_form(user, None, "mold_new")


# ---------------------------------------------------------------- usage

def _usage_form(user) -> None:
    opts = svc.mold_options()
    if not opts:
        st.warning("سجّل الأسطمبات الأول من سجل الأسطمبات")
        return
    c1, c2, c3, c4 = st.columns(4)
    mold_id = c1.selectbox("الأسطمبة", list(opts), format_func=opts.get, key="mu_mold")
    m = svc.get_mold(mold_id)
    usage_date = c2.date_input("التاريخ", value=date.today(), format="YYYY-MM-DD", key="mu_date")
    shots = c3.number_input("عدد الكبسات", min_value=0.0, step=10.0, key="mu_shots")
    cav = c4.number_input("التجاويف الشغالة", min_value=1, max_value=int(m["cavities"]), value=int(m["cavities"]),
                          step=1, key=f"mu_cav_{mold_id}", help="قلّلها لو فيه تجويف مقفول")
    c5, c6 = st.columns(2)
    finished = items.item_options(("finished",))
    item_id = c5.selectbox("المنتج (اختياري)", list(finished), format_func=finished.get, index=None,
                           placeholder="—", key="mu_item")
    memo = c6.text_input("البيان", key="mu_memo")
    pieces = shots * cav
    per = 2 if m["counter_unit"] == "pair" else 1
    st.caption(f"= {pieces:,.0f} قطعة = **{pieces / per:,.2f} {COUNTER_UNITS[m['counter_unit']]}** على العداد · "
               f"الباقي من العمر قبل التسجيل {m['position']['remaining']:,.0f}")
    if st.button("حفظ التشغيل", type="primary", key="mu_save"):
        if run_action(svc.save_usage, user, None, mold_id, usage_date, shots, cav, item_id, memo,
                      success="تم تسجيل التشغيل"):
            _clear("mu_shots")
            st.rerun()


def render_usage() -> None:
    user = guard(svc.USAGE_SCREEN)
    page_header("عداد تشغيل الأسطمبات", "بيتسجل بالكبسات × التجاويف الشغالة ويتحول لوحدة عداد الأسطمبة")
    if user.can(svc.USAGE_SCREEN, "add"):
        with st.container(border=True):
            _usage_form(user)
    c1, c2, c3 = st.columns(3)
    opts = svc.mold_options(active_only=False)
    mold_id = c1.selectbox("فلتر بالأسطمبة", list(opts), format_func=opts.get, index=None, placeholder="الكل",
                           key="mu_f_mold")
    d_from = c2.date_input("من", value=date(date.today().year, date.today().month, 1), format="YYYY-MM-DD",
                           key="mu_f_from")
    d_to = c3.date_input("إلى", value=date.today(), format="YYYY-MM-DD", key="mu_f_to")
    df = svc.usage_frame(mold_id, d_from, d_to)
    if df.empty:
        st.info("مفيش تشغيل في الفترة")
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch",
                 column_config={"الكبسات": QTY, "القطع": QTY})
    export_buttons(df.drop(columns=["id"]), "تشغيل الأسطمبات", svc.USAGE_SCREEN, "mold_usage",
                   subtitle=f"من {d_from} إلى {d_to}")
    if user.can(svc.USAGE_SCREEN, "delete"):
        with st.expander("حذف سطر تشغيل"):
            labels = {int(r["id"]): f"{r['التاريخ']} - {r['الأسطمبة']} - {r['الكبسات']:g} كبسة" for _, r in df.iterrows()}
            uid = st.selectbox("السطر", list(labels), format_func=labels.get, key="mu_del_pick")
            if st.button("تأكيد الحذف", key="mu_del"):
                if run_action(svc.delete_usage, user, uid, success="تم الحذف"):
                    st.rerun()


# ---------------------------------------------------------------- maintenance

def _counter_accounts() -> dict[int, str]:
    from sqlalchemy import select

    from core.db import session_scope
    from core.models import Account

    out = dict(cash.treasury_accounts())
    with session_scope() as s:
        ap = s.scalar(select(Account).where(Account.system_key == "ap"))
        out[ap.id] = f"{ap.code} - {ap.name_ar} (آجل)"
    return out


def _maint_form(user, doc: dict | None, key: str) -> None:
    opts = svc.mold_options()
    if not opts:
        st.warning("سجّل الأسطمبات الأول")
        return
    v = doc or {}
    c1, c2, c3 = st.columns(3)
    mold_id = _pick("الأسطمبة", opts, v.get("mold_id"), f"{key}_mold", c1)
    doc_date = c2.date_input("التاريخ", value=v.get("doc_date") or date.today(), format="YYYY-MM-DD",
                             key=f"{key}_date")
    amount = c3.number_input("المبلغ", min_value=0.0, step=50.0, format="%.2f", value=float(v.get("amount") or 0),
                             key=f"{key}_amt")
    kinds = list(MAINTENANCE_KINDS)
    kind = st.radio("النوع", kinds, format_func=MAINTENANCE_KINDS.get, horizontal=True, key=f"{key}_kind",
                    index=kinds.index(v.get("kind", "routine")))
    extra = 0.0
    if kind == OVERHAUL:
        unit = COUNTER_UNITS[svc.get_mold(mold_id)["counter_unit"]] if mold_id else "وحدة"
        extra = st.number_input(f"العمر الإضافي ({unit})", min_value=0.0, step=1000.0,
                                value=float(v.get("extra_life") or 0), key=f"{key}_extra",
                                help="المبلغ بيتضاف على تكلفة الأسطمبة (1106) والإهلاك بيتعاد حسابه من الشهر ده")
    accounts = _counter_accounts()
    c4, c5, c6 = st.columns(3)
    counter = c4.selectbox("اتدفعت من / مستحقة لـ", list(accounts), format_func=accounts.get, key=f"{key}_acc",
                           index=list(accounts).index(v["counter_account_id"])
                           if v.get("counter_account_id") in accounts else 0)
    party = None
    if accounts.get(counter, "").startswith("2101"):
        sup = parties.options(SUPPLIER)
        party = c5.selectbox("المورد", list(sup), format_func=sup.get, key=f"{key}_party",
                             index=list(sup).index(v["party_id"]) if v.get("party_id") in sup else None,
                             placeholder="اختار المورد")
    memo = c6.text_input("البيان", value=v.get("memo") or "", key=f"{key}_memo")
    if st.button("حفظ وإرسال للاعتماد", type="primary", key=f"{key}_save"):
        if mold_id is None:
            st.error("اختار الأسطمبة")
            return
        if run_action(docs.save_maintenance, user, v.get("id"), mold_id, doc_date, kind, amount, counter, party,
                      extra, memo, success="تم الحفظ. في انتظار الاعتماد."):
            if doc is None:
                _clear(f"{key}_")
            st.rerun()


def _maint_list(user) -> None:
    df = docs.maintenance_frame(user)
    if df.empty:
        st.info("مفيش مستندات صيانة")
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch", column_config={"المبلغ": MONEY})
    export_buttons(df.drop(columns=["id"]), "صيانة وتعمير الأسطمبات", docs.MAINT_SCREEN, "mold_maintenance",
                   totals={"المبلغ": float(df["المبلغ"].sum())})
    _open_docs(user, df, docs.MAINT_SCREEN, "mm", docs.get_maintenance, docs.delete_maintenance,
               lambda d, k: _maint_form(user, d, k))


def _open_docs(user, df, screen, prefix, getter, deleter, form=None) -> None:
    open_docs = df[df["الحالة"].isin(EDITABLE)]
    if open_docs.empty or not (user.can(screen, "edit") or user.can(screen, "delete")):
        return
    st.divider()
    st.subheader("تعديل أو حذف مستند لم يُعتمد")
    labels = {int(r["id"]): f"{r['رقم المستند']} - {r.get('الأسطمبة', r.get('الشهر', ''))} ({r['الحالة']})"
              for _, r in open_docs.iterrows()}
    doc_id = st.selectbox("المستند", list(labels), format_func=labels.get, key=f"{prefix}_pick")
    doc = getter(doc_id)
    if doc.get("rejection_note"):
        st.warning(f"ملاحظة المراجع: {doc['rejection_note']}")
    if form and user.can(screen, "edit"):
        form(doc, f"{prefix}_edit_{doc_id}")
    if user.can(screen, "delete"):
        with st.expander("حذف المستند"):
            if st.button("تأكيد الحذف", key=f"{prefix}_del_{doc_id}"):
                if run_action(deleter, user, doc_id, success="تم الحذف"):
                    st.rerun()


def render_maintenance() -> None:
    user = guard(docs.MAINT_SCREEN)
    page_header("صيانة وتعمير الأسطمبات", "الصيانة الدورية مصروف 5205 · التعمير بيتضاف على التكلفة 1106 ويزوّد العمر")
    tabs = ["المستندات"] + (["مستند جديد"] if user.can(docs.MAINT_SCREEN, "add") else [])
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            _maint_list(user) if name == "المستندات" else _maint_form(user, None, "mm_new")


# ---------------------------------------------------------------- amortization

def render_amortization() -> None:
    user = guard(docs.AMORT_SCREEN)
    page_header("استهلاك الأسطمبات الشهري", "وحدات الإنتاج × (صافي القيمة القابلة للإهلاك ÷ العمر الباقي) · "
                "مدين 5209 / دائن 1206")
    if user.can(docs.AMORT_SCREEN, "add"):
        with st.container(border=True):
            today = date.today()
            prev = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
            c1, c2, _ = st.columns([1, 1, 3])
            year = c1.number_input("السنة", min_value=2000, max_value=2100, value=prev[0], step=1, key="ma_year")
            month = c2.number_input("الشهر", min_value=1, max_value=12, value=prev[1], step=1, key="ma_month")
            plan = docs.preview_month(int(year), int(month))
            if plan.empty:
                st.info("مفيش تشغيل على أسطمبات المصنع في الشهر ده")
            else:
                st.dataframe(plan, hide_index=True, width="stretch",
                             column_config={"القيمة": MONEY, "صافي القيمة قبل الشهر": MONEY})
                st.markdown(f"**إجمالي إهلاك الشهر: {plan['القيمة'].sum():,.2f}**")
                if st.button("احسب وابعت للاعتماد", type="primary", key="ma_compute"):
                    if run_action(docs.compute_month, user, int(year), int(month),
                                  success="تم حساب الإهلاك. في انتظار الاعتماد."):
                        st.rerun()
    df = docs.amortizations_frame(user)
    if df.empty:
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch", column_config={"القيمة": MONEY})
    export_buttons(df.drop(columns=["id"]), "استهلاك الأسطمبات", docs.AMORT_SCREEN, "mold_amortization")
    labels = {int(r["id"]): f"{r['رقم المستند']} - {r['الشهر']} ({r['الحالة']})" for _, r in df.iterrows()}
    doc_id = st.selectbox("تفاصيل المستند", list(labels), format_func=labels.get, key="ma_pick")
    st.dataframe(docs.amortization_lines(doc_id), hide_index=True, width="stretch", column_config={"القيمة": MONEY})
    with st.expander("القيد"):
        st.dataframe(approval.preview("mold_amortization", doc_id), hide_index=True, width="stretch")
    if df.loc[df["id"] == doc_id, "الحالة"].iloc[0] in EDITABLE and user.can(docs.AMORT_SCREEN, "delete"):
        if st.button("حذف المستند", key=f"ma_del_{doc_id}"):
            if run_action(docs.delete_amortization, user, doc_id, success="تم الحذف"):
                st.rerun()


# ---------------------------------------------------------------- settlement

def _settle_form(user, doc: dict | None, key: str) -> None:
    opts = svc.mold_options(ownership=AMORTIZED + (CUSTOMER_OWNED,))
    if not opts:
        st.warning("مفيش أسطمبات شغالة")
        return
    v = doc or {}
    c1, c2 = st.columns([2, 1])
    mold_id = _pick("الأسطمبة", opts, v.get("mold_id"), f"{key}_mold", c1)
    doc_date = c2.date_input("تاريخ التسوية", value=v.get("doc_date") or date.today(), format="YYYY-MM-DD",
                             key=f"{key}_date")
    if mold_id is None:
        return
    pv = docs.settlement_preview(mold_id, doc_date)
    unit = pv["unit"]
    k = st.columns(4)
    if pv["ownership"] == AGREED:
        k[0].metric(f"المتفق عليه ({unit})", f"{pv['agreed']:,.0f}")
        k[1].metric("اتشغّل", f"{pv['used']:,.0f}")
        k[2].metric("العجز عن الاتفاق", f"{pv['short']:,.0f}")
    k[3].metric("صافي القيمة الدفترية", f"{pv['book_value']:,.2f}")
    c3, c4, c5 = st.columns(3)
    recovery = c3.number_input("مبلغ يتحمله العميل", min_value=0.0, step=100.0, format="%.2f",
                               value=float(v.get("recovery_amount") or 0), key=f"{key}_rec")
    cus = parties.options(CUSTOMER)
    party = None
    if recovery:
        party = c4.selectbox("العميل", list(cus), format_func=cus.get, key=f"{key}_party",
                             index=list(cus).index(v.get("party_id") or pv["customer_id"])
                             if (v.get("party_id") or pv["customer_id"]) in cus else None, placeholder="اختار العميل")
    retire = c5.checkbox("شطب الأسطمبة (خروجها من الخدمة)", value=bool(v.get("retire")), key=f"{key}_ret")
    memo = st.text_input("البيان", value=v.get("memo") or "", key=f"{key}_memo")
    rows = []
    if recovery:
        rows += [{"الحساب": "1401 العملاء", "مدين": recovery, "دائن": 0.0},
                 {"الحساب": "4301 إيراد استرداد تكلفة أسطمبات", "مدين": 0.0, "دائن": recovery}]
    if retire and pv["ownership"] in AMORTIZED and pv["cost"]:
        rows += [{"الحساب": "1206 مجمع استهلاك الأسطمبات", "مدين": pv["accumulated"], "دائن": 0.0},
                 {"الحساب": "5401 خسائر شطب أسطمبات", "مدين": pv["book_value"], "دائن": 0.0},
                 {"الحساب": "1106 الأسطمبات", "مدين": 0.0, "دائن": pv["cost"]}]
    if rows:
        st.markdown("##### ملخص القيد قبل الحفظ")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={"مدين": MONEY, "دائن": MONEY})
        st.caption("الأرقام بتتحسب تاني وقت الاعتماد؛ لو فيه إهلاك لسه متعتمدش لازم يتعتمد الأول قبل الشطب.")
    if st.button("حفظ وإرسال للاعتماد", type="primary", key=f"{key}_save"):
        if run_action(docs.save_settlement, user, v.get("id"), mold_id, doc_date, recovery, retire, party, memo,
                      success="تم حفظ التسوية. في انتظار الاعتماد."):
            if doc is None:
                _clear(f"{key}_")
            st.rerun()


def _settle_list(user) -> None:
    df = docs.settlements_frame(user)
    if df.empty:
        st.info("مفيش تسويات")
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch",
                 column_config={"مبلغ الاسترداد": MONEY, "صافي القيمة وقت التسجيل": MONEY})
    export_buttons(df.drop(columns=["id"]), "تسويات الأسطمبات", docs.SETTLE_SCREEN, "mold_settlements")
    _open_docs(user, df, docs.SETTLE_SCREEN, "ms", docs.get_settlement, docs.delete_settlement,
               lambda d, k: _settle_form(user, d, k))


def render_settlement() -> None:
    user = guard(docs.SETTLE_SCREEN)
    page_header("تسوية الأسطمبات", "تحميل العميل بعجز الحد الأدنى (1401 / 4301) و/أو شطب صافي القيمة (5401)")
    tabs = ["التسويات"] + (["تسوية جديدة"] if user.can(docs.SETTLE_SCREEN, "add") else [])
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            _settle_list(user) if name == "التسويات" else _settle_form(user, None, "ms_new")


# ---------------------------------------------------------------- report

def render_report() -> None:
    guard("mold_report")
    page_header("تقرير الأسطمبات", "القيمة والعمر والاستهلاك والاتفاقات، مع تنبيهات قرب نهاية العمر أو الاتفاق")
    c1, c2, c3 = st.columns(3)
    as_of = c1.date_input("في تاريخ", value=date.today(), format="YYYY-MM-DD", key="mr_date")
    pct = c2.number_input("نبّه لما الاستهلاك يوصل %", min_value=1.0, max_value=100.0, value=90.0, key="mr_pct")
    days = c3.number_input("نبّه قبل نهاية الاتفاق بـ (يوم)", min_value=0, value=30, step=5, key="mr_days")
    df = svc.report_frame(as_of, int(days), pct)
    if df.empty:
        st.info("مفيش أسطمبات")
        return
    warns = df[df["تنبيه"] != ""]
    for _, r in warns.iterrows():
        st.warning(f"**{r['الكود']} - {r['الأسطمبة']}**: {r['تنبيه']}")
    out = df.drop(columns=["id"])
    st.dataframe(out, hide_index=True, width="stretch",
                 column_config={c: MONEY for c in ("التكلفة", "مجمع الاستهلاك", "صافي القيمة الدفترية",
                                                   "المسترد من العملاء")})
    export_buttons(out, "تقرير الأسطمبات", "mold_report", "mold_report", subtitle=f"في {as_of}",
                   totals={c: float(df[c].sum()) for c in ("التكلفة", "مجمع الاستهلاك", "صافي القيمة الدفترية")})
    st.markdown("##### مطابقة السجل مع الأستاذ العام")
    rec = svc.reconciliation(as_of)
    st.dataframe(rec, hide_index=True, width="stretch",
                 column_config={c: MONEY for c in ("سجل الأسطمبات", "الأستاذ العام", "الفرق")})
    if rec["الفرق"].abs().sum() > 0.01:
        st.caption("الفرق غالباً من رصيد افتتاحي لسه قيده متعتمدش، أو قيد يدوي على 1106 / 1206 من غير ربط بأسطمبة.")
