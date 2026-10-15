"""Journal vouchers: written here, posted only on approval. Opening balances have their own screen."""
from datetime import date

import pandas as pd
import streamlit as st

from core.models.documents import JOURNAL_OPENING, JOURNAL_REGULAR, STATUS_LABELS
from core.services import journals as svc, parties
from ui.common import account_options, export_buttons, flash, guard, import_section, page_header, run_action

SCREEN = svc.SCREEN
MONEY = st.column_config.NumberColumn(format="%.2f", min_value=0.0, step=0.01)
EDITABLE = (STATUS_LABELS["pending"], STATUS_LABELS["returned"])


def _lines_editor(key: str, lines: list[dict], accounts: dict[int, str]) -> list[dict]:
    popts = parties.options()
    rows = [{"الحساب": accounts.get(l["account_id"]), "مدين": l["debit"] or None, "دائن": l["credit"] or None,
             "العميل / المورد": popts.get(l.get("party_id")), "البيان": l.get("memo", "")} for l in lines] or \
        [{"الحساب": None, "مدين": None, "دائن": None, "العميل / المورد": None, "البيان": ""}] * 2
    edited = st.data_editor(
        # The editable grid lays out left to right; reverse so the account column sits on the right.
        pd.DataFrame(rows).astype({"مدين": "float", "دائن": "float"}).iloc[:, ::-1], key=key,
        num_rows="dynamic", hide_index=True, width="stretch",
        column_config={
            "الحساب": st.column_config.SelectboxColumn(options=list(accounts.values()), width="large", required=True),
            "مدين": MONEY, "دائن": MONEY,
            "العميل / المورد": st.column_config.SelectboxColumn(options=list(popts.values()),
                                                                help="مطلوب على حسابي العملاء 1401 والموردين 2101"),
            "البيان": st.column_config.TextColumn(width="medium"),
        },
    )
    by_label = {v: k for k, v in accounts.items()}
    party_ids = {v: k for k, v in popts.items()}
    out = [{"account_id": by_label.get(r["الحساب"]), "debit": r["مدين"] if pd.notna(r["مدين"]) else 0,
            "credit": r["دائن"] if pd.notna(r["دائن"]) else 0, "memo": r["البيان"] if isinstance(r["البيان"], str) else "",
            "party_id": party_ids.get(r["العميل / المورد"])}
           for r in edited.to_dict("records")]
    dr = sum(float(l["debit"] or 0) for l in out)
    cr = sum(float(l["credit"] or 0) for l in out)
    c1, c2, c3 = st.columns(3)
    c1.metric("إجمالي المدين", f"{dr:,.2f}")
    c2.metric("إجمالي الدائن", f"{cr:,.2f}")
    c3.metric("الفرق", f"{dr - cr:,.2f}")
    return out


def _form(user, journal: dict | None, kind: str, key: str) -> None:
    accounts = account_options()
    c1, c2 = st.columns([1, 3])
    doc_date = c1.date_input("التاريخ", value=journal["doc_date"] if journal else date.today(), key=f"{key}_date",
                             format="YYYY-MM-DD")
    memo = c2.text_input("البيان", value=journal["memo"] if journal else "", key=f"{key}_memo")
    lines = _lines_editor(f"{key}_lines", journal["lines"] if journal else [], accounts)
    auto = False
    if kind == JOURNAL_OPENING:
        auto = st.checkbox("وازن الفرق تلقائياً على حساب أرصدة افتتاحية - حقوق ملكية (3303)", value=True,
                           key=f"{key}_auto")
    label = "حفظ التعديل وإرساله للاعتماد" if journal else "حفظ وإرسال للاعتماد"
    if st.button(label, type="primary", key=f"{key}_save"):
        new_id = run_action(svc.save_journal, user, journal["id"] if journal else None, doc_date, memo, kind,
                            lines, auto)
        if new_id:
            flash("تم الحفظ. القيد في انتظار الاعتماد ولن يؤثر في الأرصدة قبل اعتماده.")
            st.session_state.pop(f"{key}_lines", None)
            st.rerun()


def _list_tab(user, kind: str = JOURNAL_REGULAR, screen: str = SCREEN, prefix: str = "mj") -> None:
    df = svc.journals_frame(user, kind)
    if df.empty:
        st.info("لا توجد قيود")
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch",
                 column_config={"القيمة": st.column_config.NumberColumn(format="%.2f")})
    title = "قيود الأرصدة الافتتاحية" if kind == JOURNAL_OPENING else "قيود اليومية"
    export_buttons(df.drop(columns=["id"]), title, screen, f"journals_{kind}")

    open_docs = df[df["الحالة"].isin(EDITABLE)]
    if open_docs.empty or not (user.can(screen, "edit") or user.can(screen, "delete")):
        return
    st.divider()
    st.subheader("تعديل أو حذف قيد لم يُعتمد")
    labels = {int(r["id"]): f"{r['رقم القيد']} - {r['البيان']} ({r['الحالة']})" for _, r in open_docs.iterrows()}
    doc_id = st.selectbox("القيد", list(labels), format_func=labels.get, key=f"{prefix}_pick")
    journal = svc.get_journal(doc_id)
    if journal["rejection_note"]:
        st.warning(f"ملاحظة المراجع: {journal['rejection_note']}")
    if user.can(screen, "edit"):
        _form(user, journal, journal["kind"], f"{prefix}_edit_{doc_id}")
    if user.can(screen, "delete"):
        with st.expander("حذف القيد"):
            if st.button("تأكيد الحذف", key=f"{prefix}_del_{doc_id}"):
                if run_action(svc.delete_journal, user, doc_id, success="تم حذف القيد"):
                    st.rerun()


def _import_tab(user) -> None:
    c1, c2 = st.columns([1, 3])
    doc_date = c1.date_input("التاريخ", value=date.today(), key="mj_imp_date", format="YYYY-MM-DD")
    memo = c2.text_input("البيان", key="mj_imp_memo")
    import_section(
        SCREEN, svc.lines_template(), "سطور قيد", svc.validate_lines_import,
        lambda u, df: svc.import_journal(u, df, doc_date, memo, JOURNAL_REGULAR),
        help_text="كل الملف بيتحول لقيد واحد في انتظار الاعتماد. اكتب كود الحساب الفرعي والمبلغ في عمود مدين أو دائن. "
                  "سطور حسابي العملاء 1401 والموردين 2101 لازم يتكتب فيها اسم العميل / المورد زي ما هو مسجل.",
    )


def render() -> None:
    user = guard(SCREEN)
    page_header("قيود اليومية", "كل قيد بيتحفظ في انتظار الاعتماد · الأرصدة الافتتاحية ليها شاشة لوحدها")
    tabs = ["القيود"]
    if user.can(SCREEN, "add"):
        tabs.append("قيد يومية جديد")
    if user.can(SCREEN, "add") and user.can(SCREEN, "import"):
        tabs.append("استيراد من Excel")
    handlers = {
        "القيود": _list_tab,
        "قيد يومية جديد": lambda u: _form(u, None, JOURNAL_REGULAR, "mj_new"),
        "استيراد من Excel": _import_tab,
    }
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            handlers[name](user)
