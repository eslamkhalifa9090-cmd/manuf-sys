"""Receipt and payment vouchers on cash, wallets and bank."""
from datetime import date

import streamlit as st

from core.models.documents import PAYMENT, RECEIPT, STATUS_LABELS, VOUCHER_KINDS
from core.models.parties import CUSTOMER, SUPPLIER
from core.services import cash as svc, parties
from ui.common import account_options, export_buttons, guard, page_header, run_action

SCREEN = svc.SCREEN
EDITABLE = (STATUS_LABELS["pending"], STATUS_LABELS["returned"])


def _control_accounts() -> dict[int, str]:
    """{account id: party kind} for the customer and supplier control accounts."""
    from sqlalchemy import select

    from core.db import session_scope
    from core.models import Account

    with session_scope() as s:
        rows = s.execute(select(Account.id, Account.system_key).where(Account.system_key.in_(("ar", "ap")))).all()
    return {i: (CUSTOMER if k == "ar" else SUPPLIER) for i, k in rows}


def _form(user, voucher: dict | None, kind: str, key: str) -> None:
    """Plain widgets (not st.form) so the customer/supplier list follows the chosen account at once."""
    treasuries = svc.treasury_accounts()
    accounts = account_options()
    if not treasuries:
        st.error("لا توجد حسابات نقدية فرعية تحت المجموعة 15")
        return
    v = voucher or {}
    c1, c2, c3 = st.columns(3)
    doc_date = c1.date_input("التاريخ", value=v.get("doc_date") or date.today(), format="YYYY-MM-DD", key=f"{key}_date")
    t_ids = list(treasuries)
    treasury = c2.selectbox("الخزينة / المحفظة / البنك", t_ids, format_func=treasuries.get, key=f"{key}_tre",
                            index=t_ids.index(v["treasury_account_id"]) if v.get("treasury_account_id") in t_ids else 0)
    amount = c3.number_input("المبلغ", min_value=0.0, step=1.0, format="%.2f", value=float(v.get("amount") or 0),
                             key=f"{key}_amount")
    a_ids = list(accounts)
    counter_label = ("الحساب الدائن (العملاء أو مصدر الفلوس)" if kind == RECEIPT
                     else "الحساب المدين (الموردين أو المصروف)")
    counter = st.selectbox(counter_label, a_ids, format_func=accounts.get, key=f"{key}_counter",
                           index=a_ids.index(v["counter_account_id"]) if v.get("counter_account_id") in a_ids else None,
                           placeholder="اختار الحساب")
    party_kind = _control_accounts().get(counter)
    c4, c5, c6 = st.columns(3)
    party_id, discount = None, 0.0
    if party_kind:
        popts = parties.options(party_kind)
        pids = list(popts)
        party_id = c4.selectbox("العميل" if party_kind == CUSTOMER else "المورد", pids, format_func=popts.get,
                                index=pids.index(v["party_id"]) if v.get("party_id") in pids else None,
                                placeholder="اختار من القائمة", key=f"{key}_party_{party_kind}")
        if not popts:
            c4.caption("سجّلهم الأول من شاشة العملاء والموردين")
        wants_discount = (kind == RECEIPT and party_kind == CUSTOMER) or (kind == PAYMENT and party_kind == SUPPLIER)
        if wants_discount:
            discount = c6.number_input("خصم مسموح به" if kind == RECEIPT else "خصم مكتسب", min_value=0.0, step=1.0,
                                       format="%.2f", value=float(v.get("discount") or 0), key=f"{key}_disc")
    party = c5.text_input("اسم حر (اختياري)" if party_kind else "الجهة (اسم حر)", value=v.get("party_name") or "",
                          key=f"{key}_pname")
    c7, c8 = st.columns(2)
    reference = c7.text_input("المرجع (رقم فاتورة / إيصال)", value=v.get("reference") or "", key=f"{key}_ref")
    memo = c8.text_input("البيان", value=v.get("memo") or "", key=f"{key}_memo")
    if st.button("حفظ وإرسال للاعتماد", type="primary", key=f"{key}_save"):
        if counter is None:
            st.error("اختار الحساب المقابل")
            return
        if run_action(svc.save_voucher, user, v.get("id"), kind, doc_date, treasury, counter, amount,
                      party, reference, memo, party_id, discount,
                      success=f"تم حفظ {VOUCHER_KINDS[kind]}. في انتظار الاعتماد."):
            if voucher is None:
                for k in [k for k in st.session_state if str(k).startswith(f"{key}_")]:
                    del st.session_state[k]
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
