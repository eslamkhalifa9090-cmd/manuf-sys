"""Receipt and payment vouchers on cash, wallets and bank."""
from datetime import date

import streamlit as st

from core.models.documents import PAYMENT, RECEIPT, STATUS_LABELS, VOUCHER_KINDS
from core.models.parties import CUSTOMER, SUPPLIER
from core.services import cash as svc, parties
from ui.common import account_options, export_buttons, guard, page_header, run_action

SCREEN = svc.SCREEN
EDITABLE = (STATUS_LABELS["pending"], STATUS_LABELS["returned"])


def _form(user, voucher: dict | None, kind: str, key: str) -> None:
    treasuries = svc.treasury_accounts()
    accounts = account_options()
    if not treasuries:
        st.error("لا توجد حسابات نقدية فرعية تحت المجموعة 15")
        return
    v = voucher or {}
    with st.form(key, clear_on_submit=voucher is None):
        c1, c2, c3 = st.columns(3)
        doc_date = c1.date_input("التاريخ", value=v.get("doc_date") or date.today(), format="YYYY-MM-DD")
        t_ids = list(treasuries)
        treasury = c2.selectbox("الخزينة / المحفظة / البنك", t_ids, format_func=treasuries.get,
                                index=t_ids.index(v["treasury_account_id"]) if v.get("treasury_account_id") in t_ids else 0)
        amount = c3.number_input("المبلغ", min_value=0.0, step=1.0, format="%.2f", value=float(v.get("amount") or 0))
        a_ids = list(accounts)
        counter_label = ("الحساب الدائن (العملاء أو مصدر الفلوس)" if kind == RECEIPT
                         else "الحساب المدين (الموردين أو المصروف)")
        counter = st.selectbox(counter_label, a_ids, format_func=accounts.get,
                               index=a_ids.index(v["counter_account_id"]) if v.get("counter_account_id") in a_ids else None,
                               placeholder="اختار الحساب")
        c4, c5, c6 = st.columns(3)
        popts = parties.options(CUSTOMER if kind == RECEIPT else SUPPLIER)
        pids = list(popts)
        party_id = c4.selectbox("العميل" if kind == RECEIPT else "المورد", pids, format_func=popts.get,
                                index=pids.index(v["party_id"]) if v.get("party_id") in pids else None,
                                placeholder="مطلوب لو الحساب عملاء أو موردين")
        party = c5.text_input("أو جهة أخرى (اسم حر)", value=v.get("party_name") or "")
        discount = c6.number_input("خصم مسموح به" if kind == RECEIPT else "خصم مكتسب", min_value=0.0, step=1.0,
                                   format="%.2f", value=float(v.get("discount") or 0),
                                   help="بيتسجل بس مع " + ("التحصيل من العملاء" if kind == RECEIPT else "السداد للموردين"))
        c7, c8 = st.columns(2)
        reference = c7.text_input("المرجع (رقم فاتورة / إيصال)", value=v.get("reference") or "")
        memo = c8.text_input("البيان", value=v.get("memo") or "")
        if st.form_submit_button("حفظ وإرسال للاعتماد", type="primary"):
            if counter is None:
                st.error("اختار الحساب المقابل")
                return
            if run_action(svc.save_voucher, user, v.get("id"), kind, doc_date, treasury, counter, amount,
                          party, reference, memo, party_id, discount,
                          success=f"تم حفظ {VOUCHER_KINDS[kind]}. في انتظار الاعتماد."):
                st.rerun()


def _list_tab(user) -> None:
    kinds = {"الكل": None} | {v: k for k, v in VOUCHER_KINDS.items()}
    kind = kinds[st.radio("النوع", list(kinds), horizontal=True, key="cv_kind")]
    df = svc.vouchers_frame(user, kind)
    if df.empty:
        st.info("لا توجد سندات")
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch",
                 column_config={"المبلغ": st.column_config.NumberColumn(format="%.2f")})
    export_buttons(df.drop(columns=["id"]), "سندات القبض والصرف", SCREEN, "cash_vouchers",
                   totals={"المبلغ": float(df["المبلغ"].sum())})
    open_docs = df[df["الحالة"].isin(EDITABLE)]
    if open_docs.empty or not (user.can(SCREEN, "edit") or user.can(SCREEN, "delete")):
        return
    st.divider()
    st.subheader("تعديل أو حذف سند لم يُعتمد")
    labels = {int(r["id"]): f"{r['رقم السند']} - {r['الجهة']} - {r['المبلغ']:,.2f} ({r['الحالة']})"
              for _, r in open_docs.iterrows()}
    doc_id = st.selectbox("السند", list(labels), format_func=labels.get, key="cv_pick")
    voucher = svc.get_voucher(doc_id)
    if voucher["rejection_note"]:
        st.warning(f"ملاحظة المراجع: {voucher['rejection_note']}")
    if user.can(SCREEN, "edit"):
        _form(user, voucher, voucher["kind"], f"cv_edit_{doc_id}")
    if user.can(SCREEN, "delete"):
        with st.expander("حذف السند"):
            if st.button("تأكيد الحذف", key=f"cv_del_{doc_id}"):
                if run_action(svc.delete_voucher, user, doc_id, success="تم حذف السند"):
                    st.rerun()


def render() -> None:
    user = guard(SCREEN)
    page_header("سندات القبض والصرف", "كاش، فودافون كاش، إنستاباي، بنك")
    tabs = ["السندات"] + (["سند قبض", "سند صرف"] if user.can(SCREEN, "add") else [])
    handlers = {
        "السندات": _list_tab,
        "سند قبض": lambda u: _form(u, None, RECEIPT, "cv_new_receipt"),
        "سند صرف": lambda u: _form(u, None, PAYMENT, "cv_new_payment"),
    }
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            handlers[name](user)
