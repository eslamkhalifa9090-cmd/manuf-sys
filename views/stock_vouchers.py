"""Stock vouchers: opening stock, gifts and samples, issues, and stock-count adjustments."""
from datetime import date

import pandas as pd
import streamlit as st

from core.models.documents import STATUS_LABELS
from core.models.inventory import COUNT_GAIN, GIFT_IN, GIFT_OUT, ISSUE, STOCK_KINDS, STOCK_OPENING
from core.services import items, parties, stock_vouchers as svc
from ui.common import account_options, export_buttons, flash, guard, import_section, page_header, run_action

SCREEN = svc.SCREEN
EDITABLE = (STATUS_LABELS["pending"], STATUS_LABELS["returned"])
HELP = {
    STOCK_OPENING: "رصيد المخزن في تاريخ بداية التشغيل بالكمية وسعر الوحدة. القيد: من المخزون إلى أرصدة افتتاحية (3303).",
    GIFT_IN: "هدايا وعينات واردة بتكلفة صفر على أصناف مجموعة «عينات وهدايا واردة» بس، عشان متأثرش على متوسط الخامات. "
             "مفيش قيد مالي.",
    GIFT_OUT: "هدايا وعينات لعميل: بتخرج بمتوسط التكلفة وبتتحمل على مصروفات تسويقية — هدايا وعينات (7102).",
    ISSUE: "صرف من المخزن على مصروف أو تكلفة (مثلاً قطع غيار على الصيانة) بمتوسط التكلفة.",
    COUNT_GAIN: "زيادة في الجرد: بتدخل بمتوسط التكلفة (أو السعر اللي تكتبه) على أرباح فروق جرد (4303).",
    "count_loss": "عجز في الجرد: بيخرج بمتوسط التكلفة على عجز فروق جرد (5403).",
}


def _lines_editor(key: str, lines: list[dict], item_opts: dict, with_cost: bool) -> list[dict]:
    rows = [{"الصنف": item_opts.get(l["item_id"]), "الكمية": l["qty"], "سعر الوحدة": l.get("unit_cost"),
             "البيان": l.get("memo", "")} for l in lines] or \
        [{"الصنف": None, "الكمية": None, "سعر الوحدة": None, "البيان": ""}] * 3
    df = pd.DataFrame(rows).astype({"الكمية": "float", "سعر الوحدة": "float"})
    if not with_cost:
        df = df.drop(columns=["سعر الوحدة"])
    edited = st.data_editor(
        # The editable grid lays out left to right; reverse so the first column sits on the right.
        df[df.columns[::-1]], key=key, num_rows="dynamic", hide_index=True, width="stretch",
        column_config={"الصنف": st.column_config.SelectboxColumn(options=list(item_opts.values()), width="large"),
                       "الكمية": st.column_config.NumberColumn(min_value=0.0, format="%.3f"),
                       "سعر الوحدة": st.column_config.NumberColumn(min_value=0.0, format="%.4f")},
    )
    ids = {v: k for k, v in item_opts.items()}
    out = []
    for r in edited.to_dict("records"):
        cost = r.get("سعر الوحدة")
        out.append({"item_id": ids.get(r["الصنف"]), "qty": r["الكمية"] if pd.notna(r["الكمية"]) else 0,
                    "unit_cost": float(cost) if cost is not None and pd.notna(cost) else None,
                    "memo": r["البيان"] if isinstance(r["البيان"], str) else ""})
    return out


def _form(user, v: dict | None, kind: str, key: str) -> None:
    v = v or {}
    st.info(HELP[kind])
    c1, c2 = st.columns([1, 2])
    doc_date = c1.date_input("التاريخ", value=v.get("doc_date") or date.today(), key=f"{key}_date", format="YYYY-MM-DD")
    party = counter = None
    if kind in (GIFT_IN, GIFT_OUT):
        opts = parties.options()
        pids = list(opts)
        party = c2.selectbox("الجهة (مورد أو عميل)", pids, format_func=opts.get, key=f"{key}_party",
                             index=pids.index(v["party_id"]) if v.get("party_id") in pids else None, placeholder="اختياري")
    if kind == ISSUE:
        accs = {k: t for k, t in account_options().items() if t[:1] in ("5", "6", "7", "8")}
        aids = list(accs)
        counter = c2.selectbox("يتحمل على حساب", aids, format_func=accs.get, key=f"{key}_acc",
                               index=aids.index(v["counter_account_id"]) if v.get("counter_account_id") in aids else None,
                               placeholder="اختار حساب المصروف أو التكلفة")
    kinds = ("samples",) if kind == GIFT_IN else None
    lines = _lines_editor(f"{key}_lines", v.get("lines", []), items.item_options(kinds),
                          with_cost=kind in (STOCK_OPENING, COUNT_GAIN))
    memo = st.text_input("البيان", value=v.get("memo", ""), key=f"{key}_memo")
    if st.button("حفظ وإرسال للاعتماد", type="primary", key=f"{key}_save"):
        if run_action(svc.save_voucher, user, v.get("id"), kind, doc_date, lines, party, counter, memo):
            flash(f"تم حفظ {STOCK_KINDS[kind]}. في انتظار الاعتماد.")
            st.session_state.pop(f"{key}_lines", None)
            st.rerun()


def _list_tab(user) -> None:
    kinds = {"الكل": None} | {v: k for k, v in STOCK_KINDS.items()}
    kind = kinds[st.selectbox("النوع", list(kinds), key="sv_kind")]
    df = svc.vouchers_frame(user, kind)
    if df.empty:
        st.info("لا توجد أذون")
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch")
    export_buttons(df.drop(columns=["id"]), "أذون المخزن", SCREEN, "stock_vouchers")
    open_docs = df[df["الحالة"].isin(EDITABLE)]
    if open_docs.empty or not (user.can(SCREEN, "edit") or user.can(SCREEN, "delete")):
        return
    st.divider()
    st.subheader("تعديل أو حذف إذن لم يُعتمد")
    labels = {int(r["id"]): f"{r['رقم الإذن']} - {r['النوع']} ({r['الحالة']})" for _, r in open_docs.iterrows()}
    doc_id = st.selectbox("الإذن", list(labels), format_func=labels.get, key="sv_pick")
    v = svc.get_voucher(doc_id)
    if v["rejection_note"]:
        st.warning(f"ملاحظة المراجع: {v['rejection_note']}")
    if user.can(SCREEN, "edit"):
        _form(user, v, v["kind"], f"sv_edit_{doc_id}")
    if user.can(SCREEN, "delete"):
        with st.expander("حذف الإذن"):
            if st.button("تأكيد الحذف", key=f"sv_del_{doc_id}"):
                if run_action(svc.delete_voucher, user, doc_id, success="تم الحذف"):
                    st.rerun()


def _new_tab(user) -> None:
    kind = st.radio("نوع الإذن", list(STOCK_KINDS), format_func=STOCK_KINDS.get, horizontal=True, key="sv_new_kind")
    _form(user, None, kind, f"sv_new_{kind}")


def _import_tab(user) -> None:
    c1, c2, c3 = st.columns([1, 1, 2])
    kinds = [STOCK_OPENING, COUNT_GAIN, "count_loss"]
    kind = c1.selectbox("نوع الإذن", kinds, format_func=STOCK_KINDS.get, key="sv_imp_kind")
    doc_date = c2.date_input("التاريخ", value=date.today(), key="sv_imp_date", format="YYYY-MM-DD")
    memo = c3.text_input("البيان", key="sv_imp_memo", value="أرصدة مخزن افتتاحية" if kind == STOCK_OPENING else "جرد")
    import_section(
        SCREEN, svc.lines_template(), "أرصدة مخزن", svc.validate_lines_import,
        lambda u, df: svc.import_voucher(u, df, kind, doc_date, memo),
        help_text="الملف كله بيتحول لإذن واحد في انتظار الاعتماد. اكتب كود الصنف أو اسمه زي ما هو مسجل، والكمية، "
                  "وسعر الوحدة (مطلوب في الرصيد الافتتاحي).",
    )


def render() -> None:
    user = guard(SCREEN)
    page_header("أذون المخزن والتسويات", "أرصدة افتتاحية، هدايا وعينات، صرف، وتسويات الجرد — بالمتوسط المرجح")
    tabs = ["الأذون"] + (["إذن جديد"] if user.can(SCREEN, "add") else []) + \
        (["استيراد من Excel"] if user.can(SCREEN, "add") and user.can(SCREEN, "import") else [])
    handlers = {"الأذون": _list_tab, "إذن جديد": _new_tab, "استيراد من Excel": _import_tab}
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            handlers[name](user)
