"""Purchase invoices and returns."""
from datetime import date

import pandas as pd
import streamlit as st

from core.models.documents import STATUS_LABELS
from core.models.inventory import CASH, CREDIT, PAYMENT_TERMS, PURCHASE_INVOICE, PURCHASE_KINDS, PURCHASE_RETURN
from core.models.parties import SUPPLIER
from core.services import cash, items, parties, purchases as svc
from ui.common import account_options, export_buttons, flash, guard, page_header, run_action

SCREEN = svc.SCREEN
EDITABLE = (STATUS_LABELS["pending"], STATUS_LABELS["returned"])
MONEY = st.column_config.NumberColumn(format="%.2f")


def _direct_accounts() -> dict[int, str]:
    """Accounts a purchase line may post to directly: fixed assets and expenses (classes 1x fixed, 5-8)."""
    return {k: v for k, v in account_options().items() if v[:2] in ("11",) or v[:1] in ("5", "6", "7", "8")}


def _lines_editor(key: str, lines: list[dict], item_opts: dict, acc_opts: dict) -> list[dict]:
    rows = [{"الصنف": item_opts.get(l["item_id"]), "أو حساب مباشر": acc_opts.get(l["account_id"]),
             "الكمية": l["qty"], "سعر الوحدة": l["unit_price"], "البيان": l.get("description", "")} for l in lines]
    if not rows:
        rows = [{"الصنف": None, "أو حساب مباشر": None, "الكمية": None, "سعر الوحدة": None, "البيان": ""}] * 3
    df = pd.DataFrame(rows).astype({"الكمية": "float", "سعر الوحدة": "float"})
    edited = st.data_editor(
        # The editable grid lays out left to right; reverse so the first column sits on the right.
        df[df.columns[::-1]], key=key, num_rows="dynamic", hide_index=True, width="stretch",
        column_config={
            "الصنف": st.column_config.SelectboxColumn(options=list(item_opts.values()), width="large"),
            "أو حساب مباشر": st.column_config.SelectboxColumn(options=list(acc_opts.values()), width="medium",
                                                              help="للأصول الثابتة والمصروفات اللي مش بتدخل المخزن"),
            "الكمية": st.column_config.NumberColumn(min_value=0.0, format="%.3f"),
            "سعر الوحدة": st.column_config.NumberColumn(min_value=0.0, format="%.4f"),
        },
    )
    item_ids = {v: k for k, v in item_opts.items()}
    acc_ids = {v: k for k, v in acc_opts.items()}
    out = []
    for r in edited.to_dict("records"):
        out.append({"item_id": item_ids.get(r["الصنف"]), "account_id": acc_ids.get(r["أو حساب مباشر"]),
                    "qty": r["الكمية"] if pd.notna(r["الكمية"]) else 0,
                    "unit_price": r["سعر الوحدة"] if pd.notna(r["سعر الوحدة"]) else 0,
                    "description": r["البيان"] if isinstance(r["البيان"], str) else ""})
    return out


def _form(user, inv: dict | None, kind: str, key: str) -> None:
    sup = parties.options(SUPPLIER)
    if not sup:
        st.warning("سجّل الموردين الأول من شاشة العملاء والموردين")
        return
    v = inv or {}
    c1, c2, c3 = st.columns(3)
    doc_date = c1.date_input("التاريخ", value=v.get("doc_date") or date.today(), key=f"{key}_date", format="YYYY-MM-DD")
    sids = list(sup)
    party = c2.selectbox("المورد", sids, format_func=sup.get, key=f"{key}_party",
                         index=sids.index(v["party_id"]) if v.get("party_id") in sids else None, placeholder="اختار المورد")
    ref = c3.text_input("رقم فاتورة المورد", value=v.get("supplier_ref", ""), key=f"{key}_ref")
    c4, c5 = st.columns(2)
    terms = list(PAYMENT_TERMS)
    payment = c4.radio("السداد", terms, format_func=PAYMENT_TERMS.get, horizontal=True, key=f"{key}_pay",
                       index=terms.index(v.get("payment", CREDIT)))
    treasury = None
    if payment == CASH:
        tre = cash.treasury_accounts()
        tids = list(tre)
        treasury = c5.selectbox("من خزينة / محفظة / بنك", tids, format_func=tre.get, key=f"{key}_tre",
                                index=tids.index(v["treasury_account_id"]) if v.get("treasury_account_id") in tids else 0)
    lines = _lines_editor(f"{key}_lines", v.get("lines", []), items.item_options(), _direct_accounts())
    gross = sum(float(l["qty"] or 0) * float(l["unit_price"] or 0) for l in lines)
    c6, c7, c8 = st.columns(3)
    discount = c6.number_input("خصم على الفاتورة", min_value=0.0, value=float(v.get("discount") or 0), key=f"{key}_disc",
                               help="بيتوزع على السطور بنسبة قيمتها ويقلل تكلفة الأصناف")
    c7.metric("الإجمالي", f"{gross:,.2f}")
    c8.metric("الصافي", f"{gross - discount:,.2f}")
    memo = st.text_input("البيان", value=v.get("memo", ""), key=f"{key}_memo")
    if st.button("حفظ وإرسال للاعتماد", type="primary", key=f"{key}_save"):
        if party is None:
            st.error("اختار المورد")
            return
        if run_action(svc.save_invoice, user, v.get("id"), kind, doc_date, party, payment, lines, discount, treasury,
                      ref, memo):
            flash(f"تم حفظ {PURCHASE_KINDS[kind]}. في انتظار الاعتماد.")
            st.session_state.pop(f"{key}_lines", None)
            st.rerun()


def _list_tab(user) -> None:
    kinds = {"الكل": None} | {v: k for k, v in PURCHASE_KINDS.items()}
    kind = kinds[st.radio("النوع", list(kinds), horizontal=True, key="pu_kind")]
    df = svc.invoices_frame(user, kind)
    if df.empty:
        st.info("لا توجد مستندات")
        return
    money = {c: MONEY for c in ("الإجمالي", "الخصم", "الصافي")}
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch", column_config=money)
    export_buttons(df.drop(columns=["id"]), "فواتير ومرتجعات المشتريات", SCREEN, "purchases",
                   totals={"الصافي": float(df["الصافي"].sum())})
    open_docs = df[df["الحالة"].isin(EDITABLE)]
    if open_docs.empty or not (user.can(SCREEN, "edit") or user.can(SCREEN, "delete")):
        return
    st.divider()
    st.subheader("تعديل أو حذف مستند لم يُعتمد")
    labels = {int(r["id"]): f"{r['رقم المستند']} - {r['المورد']} - {r['الصافي']:,.2f} ({r['الحالة']})"
              for _, r in open_docs.iterrows()}
    doc_id = st.selectbox("المستند", list(labels), format_func=labels.get, key="pu_pick")
    inv = svc.get_invoice(doc_id)
    if inv["rejection_note"]:
        st.warning(f"ملاحظة المراجع: {inv['rejection_note']}")
    if user.can(SCREEN, "edit"):
        _form(user, inv, inv["kind"], f"pu_edit_{doc_id}")
    if user.can(SCREEN, "delete"):
        with st.expander("حذف المستند"):
            if st.button("تأكيد الحذف", key=f"pu_del_{doc_id}"):
                if run_action(svc.delete_invoice, user, doc_id, success="تم الحذف"):
                    st.rerun()


def render() -> None:
    user = guard(SCREEN)
    page_header("فواتير ومرتجعات المشتريات",
                "الأصناف بتدخل المخزن بالتكلفة بعد الخصم؛ الأصول والمصروفات بتتسجل على حسابها مباشرة")
    tabs = ["المستندات"] + (["فاتورة مشتريات", "مرتجع مشتريات"] if user.can(SCREEN, "add") else [])
    handlers = {
        "المستندات": _list_tab,
        "فاتورة مشتريات": lambda u: _form(u, None, PURCHASE_INVOICE, "pu_new"),
        "مرتجع مشتريات": lambda u: (st.caption("المرتجع بيخرج من المخزن بمتوسط التكلفة؛ الفرق عن سعر المرتجع "
                                               "بيروح على فروق التكلفة 5404."),
                                    _form(u, None, PURCHASE_RETURN, "pu_ret")),
    }
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            handlers[name](user)
