"""Sales: customer orders, invoices and returns, the sales report and customer aging."""
from datetime import date

import pandas as pd
import streamlit as st

from core.models.costing import Q_ACCEPTED
from core.models.documents import STATUS_LABELS
from core.models.inventory import CASH, CREDIT, PAYMENT_TERMS
from core.models.molds import AMORTIZED
from core.models.parties import CUSTOMER
from core.models.sales import ORDER_STATUSES, SALE_INVOICE, SALE_KINDS, SALE_RETURN, SO_CANCELLED, SO_CLOSED, SO_OPEN
from core.services import approval, cash, items, molds, parties, quotes, sales as svc
from ui.common import date_range, export_buttons, flash, guard, page_header, run_action

EDITABLE = (STATUS_LABELS["pending"], STATUS_LABELS["returned"])
MONEY = st.column_config.NumberColumn(format="%.2f")


def _init(key: str, value) -> None:
    """Seed a widget's value once; the widget is then created without `value` so code can refill it."""
    if key not in st.session_state:
        st.session_state[key] = value


def _clear(prefix: str) -> None:
    for k in [k for k in st.session_state if str(k).startswith(prefix)]:
        del st.session_state[k]


def _pick(label: str, opts: dict, key: str, container=st, placeholder: str = "اختار من القائمة"):
    ids = list(opts)
    if st.session_state.get(key) not in ids:
        st.session_state.pop(key, None)
    return container.selectbox(label, ids, format_func=opts.get, index=None, placeholder=placeholder, key=key)


def _num(v) -> float:
    return float(v) if v is not None and pd.notna(v) else 0.0


def _set_lines(key: str, lines: list[dict]) -> None:
    """Replace the grid's rows (a new editor key drops the edits made on the old rows)."""
    st.session_state[f"{key}_base"] = lines
    st.session_state[f"{key}_ver"] = st.session_state.get(f"{key}_ver", 0) + 1


def _lines_grid(key: str, with_molds: bool, order_lines: dict[int, str] | None = None) -> list[dict]:
    """Item (or mold) lines; rows pulled from an order keep their order line."""
    item_opts = items.item_options()
    mold_opts = molds.mold_options(ownership=AMORTIZED) if with_molds else {}
    base = st.session_state.get(f"{key}_base") or []
    order_lines = order_lines or {}
    rows = [{"الصنف": item_opts.get(l.get("item_id")), "الأسطمبة": mold_opts.get(l.get("mold_id")),
             "من الأمر": order_lines.get(l.get("order_line_id"), ""), "الكمية": l.get("qty"),
             "السعر": l.get("unit_price"), "البيان": l.get("description") or ""} for l in base]
    if not rows:
        rows = [{"الصنف": None, "الأسطمبة": None, "من الأمر": "", "الكمية": None, "السعر": None, "البيان": ""}] * 3
    df = pd.DataFrame(rows).astype({"الكمية": "float", "السعر": "float"})
    if not with_molds:
        df = df.drop(columns=["الأسطمبة"])
    if not order_lines:
        df = df.drop(columns=["من الأمر"])
    edited = st.data_editor(
        df[df.columns[::-1]], key=f"{key}_ed_{st.session_state.get(f'{key}_ver', 0)}", num_rows="dynamic",
        hide_index=True, width="stretch",
        column_config={
            "الصنف": st.column_config.SelectboxColumn(options=list(item_opts.values()), width="large"),
            "الأسطمبة": st.column_config.SelectboxColumn(
                options=list(mold_opts.values()), width="medium",
                help="بيع أسطمبة المصنع للعميل (إيراد 4102)؛ الكمية 1 والصنف فاضي"),
            "من الأمر": st.column_config.TextColumn(disabled=True, width="small"),
            "الكمية": st.column_config.NumberColumn(min_value=0.0, format="%.3f"),
            "السعر": st.column_config.NumberColumn(min_value=0.0, format="%.4f"),
        },
    )
    item_ids = {v: k for k, v in item_opts.items()}
    mold_ids = {v: k for k, v in mold_opts.items()}
    line_ids = {v: k for k, v in order_lines.items()}
    out = []
    for r in edited.to_dict("records"):
        out.append({"item_id": item_ids.get(r.get("الصنف")), "mold_id": mold_ids.get(r.get("الأسطمبة")),
                    "order_line_id": line_ids.get(r.get("من الأمر")), "qty": _num(r.get("الكمية")),
                    "unit_price": _num(r.get("السعر")),
                    "description": r["البيان"] if isinstance(r.get("البيان"), str) else ""})
    return out


def _order_line_labels(order_id: int | None) -> dict[int, str]:
    if not order_id:
        return {}
    o = svc.get_order(order_id)
    return {l["id"]: f"{o['doc_no']} / {i}" for i, l in enumerate(o["lines"], start=1)}


# ---------------------------------------------------------------- customer orders

def _quote_options() -> dict[int, str]:
    df = quotes.quotes_frame(status=Q_ACCEPTED)
    return {int(r["id"]): f"{r['رقم العرض']} - {r['العميل']} - {r['المنتج']} ({r['الكمية']:g} {r['الوحدة']})"
            for _, r in df.iterrows()}


def _order_form(user, order: dict | None, key: str) -> None:
    customers = parties.options(CUSTOMER)
    if not customers:
        st.warning("سجّل العملاء الأول من شاشة العملاء والموردين")
        return
    v = order or {}
    if f"{key}_base" not in st.session_state:
        _set_lines(key, [l | {"order_line_id": l["id"]} for l in v.get("lines", [])])
    _init(f"{key}_party", v.get("party_id"))
    _init(f"{key}_quote", v.get("quote_id"))
    if order is None:
        qopts = _quote_options()
        if qopts:
            c0, c00 = st.columns([3, 1])
            qid = c0.selectbox("من عرض سعر اتقبل (اختياري)", list(qopts), format_func=qopts.get, index=None,
                               placeholder="من غير عرض سعر", key=f"{key}_qpick")
            c00.write("")
            if c00.button("املأ من العرض", key=f"{key}_qfill", disabled=qid is None):
                data = svc.quote_to_order(qid)
                st.session_state[f"{key}_party"] = data["party_id"]
                st.session_state[f"{key}_quote"] = qid
                _set_lines(key, data["lines"])
                st.rerun()
    c1, c2, c3, c4 = st.columns(4)
    party = _pick("العميل", customers, f"{key}_party", c1, "اختار العميل")
    order_date = c2.date_input("تاريخ الأمر", value=v.get("order_date") or date.today(), format="YYYY-MM-DD",
                               key=f"{key}_date")
    delivery = c3.date_input("تاريخ التسليم", value=v.get("delivery_date"), format="YYYY-MM-DD", key=f"{key}_del")
    ref = c4.text_input("رقم طلبية العميل", value=v.get("customer_ref", ""), key=f"{key}_ref")
    if st.session_state.get(f"{key}_quote"):
        st.caption(f"مربوط بعرض السعر رقم {st.session_state[f'{key}_quote']}؛ الربحية الفعلية هتتقارن بيه")
    # Saved rows keep their line id, so what was delivered against them stays attached.
    lines = _lines_grid(key, with_molds=False, order_lines=_order_line_labels(v.get("id")))
    for l in lines:
        l["id"] = l.pop("order_line_id")
    total = sum(l["qty"] * l["unit_price"] for l in lines)
    st.metric("قيمة الأمر", f"{total:,.2f}")
    notes = st.text_input("ملاحظات", value=v.get("notes", ""), key=f"{key}_notes")
    if st.button("حفظ الأمر", type="primary", key=f"{key}_save"):
        if party is None:
            st.error("اختار العميل")
            return
        if run_action(svc.save_order, user, v.get("id"), order_date, party, lines, delivery, ref,
                      st.session_state.get(f"{key}_quote"), notes, success="تم حفظ أمر العميل"):
            _clear(key)
            st.rerun()


def _orders_list(user) -> None:
    sts = {"الكل": None} | {v: k for k, v in ORDER_STATUSES.items()}
    c1, c2 = st.columns(2)
    status = sts[c1.radio("الحالة", list(sts), horizontal=True, index=1, key="so_f_status")]
    customers = parties.options(CUSTOMER, include_inactive=True)
    party = c2.selectbox("العميل", list(customers), format_func=customers.get, index=None, placeholder="الكل",
                         key="so_f_party")
    df = svc.orders_frame(status, party)
    if df.empty:
        st.info("مفيش أوامر")
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch",
                 column_config={"القيمة": MONEY, "% التسليم": st.column_config.ProgressColumn(
                     min_value=0, max_value=100, format="%.0f%%")})
    export_buttons(df.drop(columns=["id"]), "أوامر العملاء", svc.ORDER_SCREEN, "sales_orders")
    st.divider()
    labels = {int(r["id"]): f"{r['رقم الأمر']} - {r['العميل']} ({r['الحالة']})" for _, r in df.iterrows()}
    oid = st.selectbox("تفاصيل الأمر", list(labels), format_func=labels.get, key="so_pick")
    o = svc.get_order(oid)
    prog = svc.order_progress(oid)
    st.dataframe(prog.drop(columns=["line_id", "item_id"]), hide_index=True, width="stretch",
                 column_config={"القيمة": MONEY})
    if user.can(svc.ORDER_SCREEN, "edit"):
        moves = {SO_OPEN: [("إقفال الأمر", SO_CLOSED), ("إلغاء الأمر", SO_CANCELLED)],
                 SO_CLOSED: [("إعادة فتح", SO_OPEN)], SO_CANCELLED: [("إعادة فتح", SO_OPEN)]}[o["status"]]
        cols = st.columns(len(moves) + 2)
        for col, (label, status_to) in zip(cols, moves):
            if col.button(label, key=f"so_st_{oid}_{status_to}"):
                if run_action(svc.set_order_status, user, oid, status_to, success="تم تغيير حالة الأمر"):
                    st.rerun()
        if o["status"] == SO_OPEN:
            with st.expander("تعديل الأمر"):
                _order_form(user, o, f"so_edit_{oid}")
    if user.can(svc.ORDER_SCREEN, "delete"):
        with st.expander("حذف الأمر"):
            st.caption("الأمر اللي عليه فواتير أو أوامر إنتاج مبيتمسحش؛ اقفله أو الغيه")
            if st.button("تأكيد الحذف", key=f"so_del_{oid}"):
                if run_action(svc.delete_order, user, oid, success="تم الحذف"):
                    st.rerun()


def render_orders() -> None:
    user = guard(svc.ORDER_SCREEN)
    page_header("أوامر العملاء", "طلبية العميل بالكميات والأسعار؛ مبتعملش قيد. الفاتورة بتسحب منها وأوامر الإنتاج "
                "بتتربط بيها")
    tabs = ["الأوامر"] + (["أمر جديد"] if user.can(svc.ORDER_SCREEN, "add") else [])
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            if name == "الأوامر":
                _orders_list(user)
            else:
                _order_form(user, None, "so_new")


# ---------------------------------------------------------------- invoices and returns

def _invoice_form(user, inv: dict | None, kind: str, key: str) -> None:
    customers = parties.options(CUSTOMER)
    if not customers:
        st.warning("سجّل العملاء الأول من شاشة العملاء والموردين")
        return
    v = inv or {}
    if f"{key}_base" not in st.session_state:
        _set_lines(key, v.get("lines", []))
    _init(f"{key}_party", v.get("party_id"))
    _init(f"{key}_order", v.get("sales_order_id"))
    _init(f"{key}_orig", v.get("return_of_id"))
    c1, c2, c3 = st.columns(3)
    party = _pick("العميل", customers, f"{key}_party", c1, "اختار العميل")
    doc_date = c2.date_input("التاريخ", value=v.get("doc_date") or date.today(), format="YYYY-MM-DD",
                             key=f"{key}_date")
    dispatch = c3.text_input("رقم إذن الصرف (لو فيه دفتر)", value=v.get("dispatch_no", ""), key=f"{key}_disp")
    order_id = return_of = None
    if party is not None:
        if kind == SALE_INVOICE:
            orders = svc.order_options(party)
            if v.get("sales_order_id") and v["sales_order_id"] not in orders:
                orders = svc.order_options(party, open_only=False)
            c4, c5 = st.columns([3, 1])
            order_id = _pick("أمر العميل (اختياري)", orders, f"{key}_order", c4, "من غير أمر")
            c5.write("")
            if c5.button("سحب من أمر العميل", key=f"{key}_pull", disabled=order_id is None,
                         help="بيملأ السطور بالباقي من الأمر بأسعاره؛ عدّل الكميات حسب اللي خارج فعلاً"):
                _set_lines(key, svc.order_lines_for_invoice(order_id))
                st.rerun()
        else:
            origs = svc.invoice_options(party)
            c4, c5 = st.columns([3, 1])
            return_of = _pick("الفاتورة الأصلية", origs, f"{key}_orig", c4, "من غير فاتورة (بمتوسط التكلفة)")
            c5.write("")
            if c5.button("سحب أصناف الفاتورة", key=f"{key}_pull", disabled=return_of is None):
                o = svc.get_invoice(return_of)
                st.session_state[f"{key}_order"] = o["sales_order_id"]
                _set_lines(key, [l for l in o["lines"] if l["item_id"]])
                st.rerun()
            if return_of:
                order_id = svc.get_invoice(return_of)["sales_order_id"]
            st.caption("المرتجع بيرجع المخزن بنفس تكلفة خروجه في الفاتورة الأصلية؛ من غير فاتورة بيرجع بمتوسط التكلفة")
    lines = _lines_grid(key, with_molds=kind == SALE_INVOICE, order_lines=_order_line_labels(order_id))
    c6, c7 = st.columns(2)
    terms = list(PAYMENT_TERMS)
    _init(f"{key}_pay", v.get("payment", CREDIT))
    payment = c6.radio("السداد", terms, format_func=PAYMENT_TERMS.get, horizontal=True, key=f"{key}_pay")
    treasury = None
    if payment == CASH:
        tre = cash.treasury_accounts()
        tids = list(tre)
        treasury = c7.selectbox("الخزينة / المحفظة / البنك", tids, format_func=tre.get, key=f"{key}_tre",
                                index=tids.index(v["treasury_account_id"]) if v.get("treasury_account_id") in tids
                                else 0)
    gross = sum(l["qty"] * l["unit_price"] for l in lines)
    c8, c9, c10 = st.columns(3)
    discount = 0.0
    if kind == SALE_INVOICE:
        discount = c8.number_input("خصم مسموح به", min_value=0.0, value=float(v.get("discount") or 0),
                                   key=f"{key}_disc", help="بيتسجل على 4202 خصم مسموح به")
    c9.metric("الإجمالي", f"{gross:,.2f}")
    c10.metric("الصافي", f"{gross - discount:,.2f}")
    memo = st.text_input("البيان", value=v.get("memo", ""), key=f"{key}_memo")
    if st.button("حفظ وإرسال للاعتماد", type="primary", key=f"{key}_save"):
        if party is None:
            st.error("اختار العميل")
            return
        if run_action(svc.save_invoice, user, v.get("id"), kind, doc_date, party, payment, lines, discount, treasury,
                      order_id, return_of, dispatch, memo):
            flash(f"تم حفظ {SALE_KINDS[kind]}. المخزن مبيتأثرش غير بعد الاعتماد.")
            _clear(key)
            st.rerun()


def _invoices_list(user) -> None:
    kinds = {"الكل": None} | {v: k for k, v in SALE_KINDS.items()}
    kind = kinds[st.radio("النوع", list(kinds), horizontal=True, key="si_kind")]
    df = svc.invoices_frame(user, kind)
    if df.empty:
        st.info("لا توجد مستندات")
        return
    money = {c: MONEY for c in ("الإجمالي", "الخصم", "الصافي")}
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch", column_config=money)
    export_buttons(df.drop(columns=["id"]), "فواتير ومرتجعات المبيعات", svc.SCREEN, "sales_invoices",
                   totals={"الصافي": float(df["الصافي"].sum())})
    st.divider()
    labels = {int(r["id"]): f"{r['رقم المستند']} - {r['العميل']} - {r['الصافي']:,.2f} ({r['الحالة']})"
              for _, r in df.iterrows()}
    doc_id = st.selectbox("المستند", list(labels), format_func=labels.get, key="si_pick")
    inv = svc.get_invoice(doc_id)
    with st.expander("القيد"):
        try:
            st.dataframe(approval.preview("sales_invoice", doc_id), hide_index=True, width="stretch",
                         column_config={"مدين": MONEY, "دائن": MONEY})
        except Exception as exc:  # e.g. not enough stock yet: say why instead of failing the page
            st.warning(str(exc))
    if inv["status"] not in ("pending", "returned"):
        return
    if inv["rejection_note"]:
        st.warning(f"ملاحظة المراجع: {inv['rejection_note']}")
    if user.can(svc.SCREEN, "edit"):
        with st.expander("تعديل المستند"):
            _invoice_form(user, inv, inv["kind"], f"si_edit_{doc_id}")
    if user.can(svc.SCREEN, "delete"):
        with st.expander("حذف المستند"):
            if st.button("تأكيد الحذف", key=f"si_del_{doc_id}"):
                if run_action(svc.delete_invoice, user, doc_id, success="تم الحذف"):
                    st.rerun()


def render_invoices() -> None:
    user = guard(svc.SCREEN)
    page_header("فواتير ومرتجعات المبيعات", "الفاتورة هي إذن الصرف: البضاعة بتخرج من المخزن بمتوسط التكلفة لما "
                "تتعتمد · مدين العميل / دائن المبيعات، ومدين تكلفة المبيعات / دائن المخزن")
    tabs = ["المستندات"] + (["فاتورة مبيعات", "مرتجع مبيعات"] if user.can(svc.SCREEN, "add") else [])
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            if name == "المستندات":
                _invoices_list(user)
            elif name == "فاتورة مبيعات":
                _invoice_form(user, None, SALE_INVOICE, "si_new")
            else:
                _invoice_form(user, None, SALE_RETURN, "si_ret")


# ---------------------------------------------------------------- reports

def render_report() -> None:
    guard("sales_report")
    page_header("تقرير المبيعات", "من الفواتير والمرتجعات المعتمدة؛ المرتجع بالسالب والصافي بعد الخصم")
    d1, d2 = date_range("srp")
    df = svc.lines_report(d1, d2)
    if df.empty:
        st.info("لا توجد مبيعات معتمدة في الفترة")
        return
    view = st.radio("العرض", ["تفصيلي", "حسب العميل", "حسب الصنف", "حسب الشهر"], horizontal=True, key="srp_view")
    vals = ["صافي المبيعات", "التكلفة", "مجمل الربح"]
    if view == "حسب العميل":
        df = df.groupby("العميل", as_index=False)[vals].sum().sort_values("صافي المبيعات", ascending=False)
    elif view == "حسب الصنف":
        df = (df.groupby(["الصنف", "الوحدة"], as_index=False)[["الكمية"] + vals].sum()
              .sort_values("صافي المبيعات", ascending=False))
        df["متوسط السعر"] = (df["صافي المبيعات"] / df["الكمية"]).where(df["الكمية"] != 0).round(4)
    elif view == "حسب الشهر":
        df = df.groupby("الشهر", as_index=False)[vals].sum()
    if view != "تفصيلي":
        df["% مجمل الربح"] = (df["مجمل الربح"] / df["صافي المبيعات"] * 100).where(df["صافي المبيعات"] != 0).round(2)
    st.dataframe(df, hide_index=True, width="stretch", column_config={c: MONEY for c in vals})
    totals = {c: float(df[c].sum()) for c in vals}
    k = st.columns(3)
    k[0].metric("صافي المبيعات", f"{totals['صافي المبيعات']:,.2f}")
    k[1].metric("تكلفة المبيعات", f"{totals['التكلفة']:,.2f}")
    margin = totals["مجمل الربح"] / totals["صافي المبيعات"] * 100 if totals["صافي المبيعات"] else 0
    k[2].metric("مجمل الربح", f"{totals['مجمل الربح']:,.2f}", f"{margin:.1f}%")
    export_buttons(df, f"تقرير المبيعات — {view}", "sales_report", "sales_report", subtitle=f"من {d1} إلى {d2}",
                   totals=totals)


def render_aging() -> None:
    guard("ar_aging")
    page_header("أعمار ديون العملاء", "رصيد كل عميل موزع على تواريخ الفواتير؛ التحصيل بيقفل الأقدم الأول")
    c1, _ = st.columns([1, 3])
    as_of = c1.date_input("في تاريخ", value=date.today(), format="YYYY-MM-DD", key="aging_date")
    df = svc.aging_frame(as_of)
    if df.empty:
        st.info("مفيش أرصدة عملاء")
        return
    money = [c for c in df.columns if c != "العميل"]
    st.dataframe(df, hide_index=True, width="stretch", column_config={c: MONEY for c in money})
    totals = {c: float(df[c].sum()) for c in money}
    k = st.columns(len(money))
    for col, c in zip(k, money):
        col.metric(c, f"{totals[c]:,.2f}")
    export_buttons(df, "أعمار ديون العملاء", "ar_aging", "ar_aging", subtitle=f"في {as_of}", totals=totals)
