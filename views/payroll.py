"""Employees, daily attendance, advances, the monthly payroll and its reports."""
from datetime import date, time

import pandas as pd
import streamlit as st

from core.models.documents import STATUS_LABELS
from core.db import session_scope
from core.models.payroll import DAILY, DAY_STATUSES, DAYS_IN_MONTH, DEPARTMENTS, MONTHLY, PAY_BASES, PRESENT, PRODUCTION
from core.services import approval, cash, payroll as svc
from ui.common import date_range, export_buttons, guard, import_section, page_header, run_action

EDITABLE = (STATUS_LABELS["pending"], STATUS_LABELS["returned"])
MONEY = st.column_config.NumberColumn(format="%.2f")
HOURS = st.column_config.NumberColumn(format="%.2f")
NO_STATUS = "—"


def _clear(prefix: str) -> None:
    for k in [k for k in st.session_state if str(k).startswith(prefix)]:
        del st.session_state[k]


def month_picker(key: str) -> tuple[int, int]:
    """Year and month, defaulting to last month."""
    today = date.today()
    prev = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    c1, c2, _ = st.columns([1, 1, 3])
    year = c1.number_input("السنة", min_value=2000, max_value=2100, value=prev[0], step=1, key=f"{key}_year")
    month = c2.number_input("الشهر", min_value=1, max_value=12, value=prev[1], step=1, key=f"{key}_month")
    return int(year), int(month)


# ---------------------------------------------------------------- employees

def _employee_form(user, e: dict | None, key: str) -> None:
    v = e or {}
    c1, c2, c3 = st.columns([2, 1, 1])
    name = c1.text_input("اسم الموظف", value=v.get("name", ""), key=f"{key}_name")
    code = c2.text_input("الكود (فاضي = تلقائي)", value=v.get("code", ""), key=f"{key}_code")
    job = c3.text_input("الوظيفة", value=v.get("job") or "", key=f"{key}_job")
    depts, bases = list(DEPARTMENTS), list(PAY_BASES)
    c4, c5 = st.columns(2)
    department = c4.radio("القسم (حساب الأجر)", depts, format_func=DEPARTMENTS.get, horizontal=True,
                          index=depts.index(v.get("department", PRODUCTION)), key=f"{key}_dept")
    basis = c5.radio("نظام الأجر", bases, format_func=PAY_BASES.get, horizontal=True,
                     index=bases.index(v.get("pay_basis", MONTHLY)), key=f"{key}_basis")
    c6, c7, c8 = st.columns(3)
    if basis == MONTHLY:
        pay = c6.number_input("الراتب الشهري", min_value=0.0, step=500.0, value=float(v.get("monthly_salary") or 0),
                              key=f"{key}_salary")
    else:
        pay = c6.number_input("اليومية", min_value=0.0, step=50.0, value=float(v.get("daily_wage") or 0),
                              key=f"{key}_wage")
    hours = c7.number_input("ساعات اليوم", min_value=1.0, max_value=24.0, step=0.5,
                            value=float(v.get("hours_per_day") or 10), key=f"{key}_hours")
    factor = c8.number_input("معامل الإضافي", min_value=0.1, step=0.25, value=float(v.get("overtime_factor") or 1),
                             key=f"{key}_factor", help="1 = الساعة الإضافي بسعر الساعة العادية، 1.5 = ساعة ونص")
    rate = (pay / (DAYS_IN_MONTH * hours) if basis == MONTHLY else pay / hours) if hours else 0
    st.caption(f"قيمة الساعة = {'الراتب ÷ (30 × ساعات اليوم)' if basis == MONTHLY else 'اليومية ÷ ساعات اليوم'} "
               f"= **{rate:,.4f}** · ساعة الإضافي = **{rate * factor:,.4f}**")
    c9, c10, c11 = st.columns(3)
    hire = c9.date_input("تاريخ التعيين", value=v.get("hire_date"), format="YYYY-MM-DD", key=f"{key}_hire")
    phone = c10.text_input("التليفون", value=v.get("phone") or "", key=f"{key}_phone")
    nid = c11.text_input("الرقم القومي", value=v.get("national_id") or "", key=f"{key}_nid")
    c12, c13 = st.columns([3, 1])
    notes = c12.text_input("ملاحظات", value=v.get("notes") or "", key=f"{key}_notes")
    active = c13.checkbox("نشط", value=bool(v.get("is_active", True)), key=f"{key}_active")
    if st.button("حفظ", type="primary", key=f"{key}_save"):
        data = {"name": name, "code": code, "job": job, "department": department, "pay_basis": basis,
                "monthly_salary": pay if basis == MONTHLY else 0, "daily_wage": pay if basis == DAILY else 0,
                "hours_per_day": hours, "overtime_factor": factor, "hire_date": hire, "phone": phone,
                "national_id": nid, "notes": notes, "is_active": active}
        if run_action(svc.save_employee, user, v.get("id"), data, success="تم حفظ الموظف"):
            if e is None:
                _clear(f"{key}_")
            st.rerun()


def _employees_tab(user) -> None:
    df = svc.employees_frame()
    if df.empty:
        st.info("لسه مفيش موظفين")
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch",
                 column_config={"الراتب / اليومية": MONEY, "قيمة الساعة": st.column_config.NumberColumn(format="%.4f")})
    export_buttons(df.drop(columns=["id"]), "الموظفين", svc.EMPLOYEE_SCREEN, "employees")
    if user.can(svc.EMPLOYEE_SCREEN, "edit") or user.can(svc.EMPLOYEE_SCREEN, "delete"):
        labels = {int(r["id"]): f"{r['الكود']} - {r['الاسم']}" for _, r in df.iterrows()}
        emp_id = st.selectbox("تعديل موظف", list(labels), format_func=labels.get, key="emp_pick")
        with st.expander("تعديل الموظف"):
            if user.can(svc.EMPLOYEE_SCREEN, "edit"):
                _employee_form(user, svc.get_employee(emp_id), f"emp_edit_{emp_id}")
            if user.can(svc.EMPLOYEE_SCREEN, "delete") and st.button("حذف الموظف", key=f"emp_del_{emp_id}"):
                if run_action(svc.delete_employee, user, emp_id, success="تم الحذف"):
                    st.rerun()


def render_employees() -> None:
    user = guard(svc.EMPLOYEE_SCREEN)
    page_header("الموظفين", "ملف كل موظف: القسم، نظام الأجر، ساعات اليوم ومعامل الإضافي — زي شيت «موظفين»")
    tabs = ["الموظفين"] + (["موظف جديد"] if user.can(svc.EMPLOYEE_SCREEN, "add") else [])
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            _employees_tab(user) if name == "الموظفين" else _employee_form(user, None, "emp_new")


# ---------------------------------------------------------------- attendance

def _load_day(key: str, work_date: date) -> None:
    st.session_state[f"{key}_base"] = svc.day_frame(work_date)
    st.session_state[f"{key}_ver"] = st.session_state.get(f"{key}_ver", 0) + 1


def _fill_present(key: str) -> None:
    """Mark everyone with no entry yet as a full day present."""
    for r in st.session_state.get(f"{key}_base") or []:
        if not r["status"]:
            r["status"] = PRESENT
    st.session_state[f"{key}_ver"] = st.session_state.get(f"{key}_ver", 0) + 1


def _day_tab(user) -> None:
    key = "att_day"
    work_date = st.date_input("اليوم", value=date.today(), format="YYYY-MM-DD", key=f"{key}_date")
    if st.session_state.get(f"{key}_loaded") != work_date:
        _load_day(key, work_date)
        st.session_state[f"{key}_loaded"] = work_date
    base = st.session_state[f"{key}_base"]
    if not base:
        st.info("سجّل الموظفين الأول من شاشة الموظفين")
        return
    labels = [NO_STATUS] + list(DAY_STATUSES.values())
    by_label = {v: k for k, v in DAY_STATUSES.items()}
    df = pd.DataFrame([{
        "الموظف": r["name"], "الحالة": DAY_STATUSES.get(r["status"], NO_STATUS),
        "الحضور": r["time_in"], "الانصراف": r["time_out"],
        "إضافي": r["overtime_hours"] if r["status"] else None, "خصم ساعات": r["deduction_hours"],
        "مكافأة": r["bonus"], "جزاء": r["penalty"], "ملاحظة": r["note"], "ساعات محسوبة": r["hours"],
    } for r in base])
    st.caption("سيب الحضور والانصراف فاضيين = يوم كامل. الإضافي فاضي = بيتحسب من الوقت الزيادة عن ساعات اليوم. "
               "الحالة «—» = مفيش تسجيل للموظف في اليوم ده.")
    cfg = {
        "الموظف": st.column_config.TextColumn(disabled=True),
        "الحالة": st.column_config.SelectboxColumn(options=labels, required=True),
        "الحضور": st.column_config.TimeColumn(format="HH:mm", step=60),
        "الانصراف": st.column_config.TimeColumn(format="HH:mm", step=60),
        "إضافي": HOURS, "خصم ساعات": HOURS, "مكافأة": MONEY, "جزاء": MONEY,
        "ساعات محسوبة": st.column_config.NumberColumn(format="%.2f", disabled=True),
    }
    edited = st.data_editor(df[df.columns[::-1]], key=f"{key}_ed_{st.session_state.get(f'{key}_ver', 0)}",
                            hide_index=True, width="stretch", column_config=cfg, num_rows="fixed")
    c1, c2, _ = st.columns([1, 1, 3])
    c2.button("الباقيين حضور يوم كامل", key=f"{key}_fill", on_click=_fill_present, args=(key,))
    if c1.button("حفظ اليوم", type="primary", key=f"{key}_save"):
        rows = []
        for r, (_, e) in zip(base, edited[df.columns].iterrows()):
            status = by_label.get(e["الحالة"])
            rows.append({"employee_id": r["employee_id"], "status": status,
                         "time_in": e["الحضور"] if isinstance(e["الحضور"], time) else None,
                         "time_out": e["الانصراف"] if isinstance(e["الانصراف"], time) else None,
                         "overtime_hours": None if pd.isna(e["إضافي"]) else e["إضافي"],
                         "deduction_hours": e["خصم ساعات"], "bonus": e["مكافأة"], "penalty": e["جزاء"],
                         "note": e["ملاحظة"]})
        # A changed time recomputes the overtime unless it was typed by hand on this save.
        for r, row in zip(base, rows):
            if (row["time_in"], row["time_out"]) != (r["time_in"], r["time_out"]) and \
                    row["overtime_hours"] == r["overtime_hours"]:
                row["overtime_hours"] = None
        saved = run_action(svc.save_day, user, work_date, rows, success="تم حفظ الحضور")
        if saved:
            _load_day(key, work_date)
            st.rerun()


def _month_tab(user) -> None:
    year, month = month_picker("att_m")
    summary = svc.attendance_summary(year, month)
    if user.can(svc.ATTENDANCE_SCREEN, "add"):
        with st.expander("تسجيل يوم الراحة الأسبوعي إجازة مدفوعة"):
            st.caption("قيمة الساعة محسوبة على 30 يوم، فالجمعة لازم تتسجل إجازة مدفوعة عشان الشهر الكامل يطلع بالراتب. "
                       "بيتسجل بس للموظفين النشطين اللي مفيش عليهم تسجيل في اليوم ده.")
            if st.button("سجّل أيام الجمعة للشهر", key="att_rest"):
                n = run_action(svc.fill_rest_days, user, year, month)
                if n is not None:
                    st.toast(f"اتسجل {0 if n is True else n} يوم")
                    st.session_state.pop("att_day_loaded", None)
                    st.rerun()
    if summary.empty:
        st.info("مفيش حضور مسجل في الشهر ده")
        return
    st.markdown("##### ملخص الشهر")
    st.dataframe(summary, hide_index=True, width="stretch", column_config={"مكافآت": MONEY, "جزاءات": MONEY})
    export_buttons(summary, "ملخص الحضور", svc.ATTENDANCE_SCREEN, "attendance_summary", subtitle=f"{month:02d}/{year}",
                   key="att_sum")
    opts = svc.employee_options(active_only=False)
    emp = st.selectbox("تفاصيل موظف", [None] + list(opts), format_func=lambda i: "كل الموظفين" if i is None else opts[i],
                       key="att_m_emp")
    detail = svc.attendance_frame(year, month, emp)
    st.dataframe(detail, hide_index=True, width="stretch", column_config={"مكافأة": MONEY, "جزاء": MONEY})
    export_buttons(detail, "الحضور والانصراف", svc.ATTENDANCE_SCREEN, "attendance", subtitle=f"{month:02d}/{year}",
                   key="att_det")


def render_attendance() -> None:
    user = guard(svc.ATTENDANCE_SCREEN)
    page_header("الحضور والانصراف", "تسجيل يومي لكل الموظفين: الحضور والانصراف، الإضافي، خصم الساعات، المكافأة "
                "والجزاء الإداري — الشهر بيتقفل لما مرتباته تتحسب")
    tabs = ["الشهر"]
    if user.can(svc.ATTENDANCE_SCREEN, "add") or user.can(svc.ATTENDANCE_SCREEN, "edit"):
        tabs.insert(0, "تسجيل يوم")
    if user.can(svc.ATTENDANCE_SCREEN, "import"):
        tabs.append("استيراد من Excel")
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            if name == "تسجيل يوم":
                _day_tab(user)
            elif name == "الشهر":
                _month_tab(user)
            else:
                import_section(svc.ATTENDANCE_SCREEN, svc.import_template(), "الحضور", svc.validate_import,
                               svc.import_attendance,
                               help_text="الموظف بالكود أو الاسم. الحالة: " + " / ".join(DAY_STATUSES.values())
                               + ". الإضافي فاضي = بيتحسب من الوقت.")


# ---------------------------------------------------------------- advances

def _advance_form(user, doc: dict | None, key: str) -> None:
    v = doc or {}
    emps = svc.employee_options()
    if not emps:
        st.warning("سجّل الموظفين الأول")
        return
    treasuries = cash.treasury_accounts()
    e_ids, t_ids = list(emps), list(treasuries)
    c1, c2, c3 = st.columns([2, 1, 2])
    employee = c1.selectbox("الموظف", e_ids, format_func=emps.get, key=f"{key}_emp",
                            index=e_ids.index(v["employee_id"]) if v.get("employee_id") in e_ids else None,
                            placeholder="اختار الموظف")
    doc_date = c2.date_input("التاريخ", value=v.get("doc_date") or date.today(), format="YYYY-MM-DD", key=f"{key}_date")
    treasury = c3.selectbox("اتصرفت من", t_ids, format_func=treasuries.get, key=f"{key}_tre",
                            index=t_ids.index(v["treasury_account_id"]) if v.get("treasury_account_id") in t_ids else 0)
    c4, c5 = st.columns(2)
    amount = c4.number_input("قيمة السلفة", min_value=0.0, step=100.0, value=float(v.get("amount") or 0),
                             key=f"{key}_amount")
    installment = c5.number_input("القسط الشهري (صفر = تتخصم كلها من أول مرتب)", min_value=0.0, step=100.0,
                                  value=float(v.get("installment") or 0), key=f"{key}_inst")
    if employee and amount:
        with session_scope() as s:
            current = svc.advance_balance(s, employee)
        st.caption(f"رصيد سلف الموظف المعتمدة قبل دي: {current:,.2f}"
                   + (f" · هتتخصم على {-(-amount // installment):.0f} شهر" if installment else ""))
    memo = st.text_input("البيان", value=v.get("memo") or "", key=f"{key}_memo")
    if st.button("حفظ وإرسال للاعتماد", type="primary", key=f"{key}_save"):
        if run_action(svc.save_advance, user, v.get("id"), doc_date, employee, amount, treasury, installment, memo,
                      success="تم حفظ السلفة. في انتظار الاعتماد."):
            if doc is None:
                _clear(f"{key}_")
            st.rerun()


def render_advances() -> None:
    user = guard(svc.ADVANCE_SCREEN)
    page_header("سلف الموظفين", "مدين 1602 سلف العاملين / دائن الخزينة · بتتخصم من المرتب بالقسط أو كلها، "
                "وتقدر تغير الخصم في كشف المرتبات حسب طلب الموظف")
    if user.can(svc.ADVANCE_SCREEN, "add"):
        with st.expander("سلفة جديدة", expanded=False):
            _advance_form(user, None, "adv_new")
    t1, t2 = st.tabs(["المستندات", "أرصدة السلف"])
    with t1:
        df = svc.advances_frame(user)
        if df.empty:
            st.info("لسه مفيش سلف")
        else:
            st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch", column_config={"المبلغ": MONEY})
            export_buttons(df.drop(columns=["id"]), "سلف الموظفين", svc.ADVANCE_SCREEN, "employee_advances")
            labels = {int(r["id"]): f"{r['رقم المستند']} - {r['الموظف']} ({r['الحالة']})" for _, r in df.iterrows()}
            doc_id = st.selectbox("تفاصيل المستند", list(labels), format_func=labels.get, key="adv_pick")
            doc = svc.get_advance(doc_id)
            with st.expander("القيد"):
                st.dataframe(approval.preview("employee_advance", doc_id), hide_index=True, width="stretch")
            if STATUS_LABELS[doc["status"]] in EDITABLE:
                if doc.get("rejection_note"):
                    st.warning(f"ملاحظة المراجع: {doc['rejection_note']}")
                if user.can(svc.ADVANCE_SCREEN, "edit"):
                    with st.expander("تعديل"):
                        _advance_form(user, doc, f"adv_edit_{doc_id}")
                if user.can(svc.ADVANCE_SCREEN, "delete") and st.button("حذف المستند", key=f"adv_del_{doc_id}"):
                    if run_action(svc.delete_advance, user, doc_id, success="تم الحذف"):
                        st.rerun()
    with t2:
        bal = svc.advance_balances()
        st.dataframe(bal, hide_index=True, width="stretch", column_config={c: MONEY for c in bal.columns[1:]})
        if len(bal):
            st.metric("إجمالي أرصدة السلف", f"{bal['الرصيد'].sum():,.2f}")


# ---------------------------------------------------------------- payroll

def _payroll_lines_editor(user, doc_id: int, sheet: pd.DataFrame) -> None:
    st.caption("عدّل المكافآت والجزاءات (إجمالي الشهر) وخصم السلفة لكل موظف، وبعدين احفظ. خصم السلفة ميزيدش عن رصيدها.")
    cols = ["الموظف", "مكافآت", "جزاءات", "خصم السلف", "ملاحظة"]
    view = sheet[cols]
    edited = st.data_editor(view[view.columns[::-1]], key=f"pay_lines_{doc_id}", hide_index=True, width="stretch",
                            num_rows="fixed", column_config={"الموظف": st.column_config.TextColumn(disabled=True),
                                                             "مكافآت": MONEY, "جزاءات": MONEY, "خصم السلف": MONEY})
    if st.button("حفظ التعديلات", key=f"pay_save_{doc_id}"):
        changed = 0
        for (_, old), (_, new) in zip(sheet.iterrows(), edited[cols].iterrows()):
            if any((old[c] or 0) != (new[c] or 0) for c in cols[1:4]) or (old["ملاحظة"] or "") != (new["ملاحظة"] or ""):
                if not run_action(svc.update_line, user, doc_id, int(old["employee_id"]), new["مكافآت"], new["جزاءات"],
                                  new["خصم السلف"], new["ملاحظة"] or ""):
                    return
                changed += 1
        if changed:
            st.toast(f"اتعدل {changed} موظف")
            st.rerun()


def render_payroll() -> None:
    user = guard(svc.PAYROLL_SCREEN)
    page_header("مسير المرتبات", "من الحضور: (الساعات + الإضافي × المعامل - خصم الساعات) × قيمة الساعة + مكافآت - "
                "جزاءات - السلف · مدين 5201 / 5202 / 6101 · دائن 1602 و 2202")
    if user.can(svc.PAYROLL_SCREEN, "add"):
        with st.container(border=True):
            year, month = month_picker("pay")
            summary = svc.attendance_summary(year, month)
            if summary.empty:
                st.info("مفيش حضور مسجل في الشهر ده")
            else:
                st.caption(f"{len(summary)} موظف عليهم حضور في الشهر. بعد الحساب الحضور بيتقفل لحد ما الكشف يتحذف "
                           "أو يتلغي اعتماده.")
                memo = st.text_input("البيان", key="pay_memo")
                if st.button("احسب الكشف وابعته للاعتماد", type="primary", key="pay_compute"):
                    if run_action(svc.compute_payroll, user, year, month, memo,
                                  success="تم حساب المرتبات. في انتظار الاعتماد."):
                        st.rerun()
    df = svc.payrolls_frame(user)
    if df.empty:
        return
    st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch",
                 column_config={c: MONEY for c in ("الإجمالي", "خصم السلف", "الصافي")})
    export_buttons(df.drop(columns=["id"]), "كشوف المرتبات", svc.PAYROLL_SCREEN, "payrolls")
    labels = {int(r["id"]): f"{r['رقم المستند']} - {r['الشهر']} ({r['الحالة']})" for _, r in df.iterrows()}
    doc_id = st.selectbox("الكشف", list(labels), format_func=labels.get, key="pay_pick")
    row = df[df["id"] == doc_id].iloc[0]
    sheet = svc.payroll_sheet(doc_id)
    money = {c: MONEY for c in ("الأساسي", "قيمة الإضافي", "قيمة الخصم", "مكافآت", "جزاءات", "المستحق", "خصم السلف",
                                "الصافي")}
    st.dataframe(sheet.drop(columns=["employee_id"]), hide_index=True, width="stretch",
                 column_config=money | {"قيمة الساعة": st.column_config.NumberColumn(format="%.4f")})
    totals = {c: round(float(sheet[c].sum()), 2) for c in money}
    k = st.columns(3)
    k[0].metric("المستحق", f"{totals['المستحق']:,.2f}")
    k[1].metric("خصم السلف", f"{totals['خصم السلف']:,.2f}")
    k[2].metric("الصافي للصرف", f"{totals['الصافي']:,.2f}")
    export_buttons(sheet.drop(columns=["employee_id"]), "كشف المرتبات", svc.PAYROLL_SCREEN, "payroll_sheet",
                   subtitle=f"شهر {row['الشهر']}", totals=totals, key=f"sheet_{doc_id}")
    editable = row["الحالة"] in EDITABLE
    if editable and user.can(svc.PAYROLL_SCREEN, "edit"):
        with st.expander("تعديل المكافآت والجزاءات وخصم السلف"):
            _payroll_lines_editor(user, doc_id, sheet)
            if st.button("أعد الحساب من الحضور", key=f"pay_recalc_{doc_id}",
                         help="بيرجع الكشف من الحضور ويلغي التعديلات اليدوية"):
                y, m = row["الشهر"].split("/")[1], row["الشهر"].split("/")[0]
                if run_action(svc.compute_payroll, user, int(y), int(m), success="تم إعادة الحساب"):
                    st.rerun()
    with st.expander("مفردات مرتب موظف"):
        names = dict(zip(sheet["employee_id"], sheet["الموظف"]))
        emp = st.selectbox("الموظف", list(names), format_func=names.get, key=f"pay_slip_{doc_id}")
        slip = svc.payslip(doc_id, int(emp))
        st.dataframe(slip, hide_index=True, width="stretch")
        export_buttons(slip, "مفردات مرتب", svc.PAYROLL_SCREEN, "payslip", subtitle=f"{names[emp]} - {row['الشهر']}",
                       key=f"slip_{doc_id}_{emp}")
    with st.expander("القيد"):
        st.dataframe(approval.preview("payroll", doc_id), hide_index=True, width="stretch")
    if row["الحالة"] == STATUS_LABELS["approved"]:
        st.info(f"صرف المرتبات: اعمل سند صرف من الخزينة على حساب 2202 أجور مستحقة بالصافي ({totals['الصافي']:,.2f}).")
    if editable and user.can(svc.PAYROLL_SCREEN, "delete"):
        if st.button("حذف الكشف", key=f"pay_del_{doc_id}"):
            if run_action(svc.delete_payroll, user, doc_id, success="تم الحذف"):
                st.rerun()


# ---------------------------------------------------------------- reports

def render_report() -> None:
    guard("payroll_report")
    page_header("تقارير الأجور", "المرتبات المعتمدة بالشهر والموظف، وأرصدة السلف")
    d1, d2 = date_range("prp")
    opts = svc.employee_options(active_only=False)
    emp = st.selectbox("الموظف", [None] + list(opts), format_func=lambda i: "كل الموظفين" if i is None else opts[i],
                       key="prp_emp")
    t1, t2, t3 = st.tabs(["المرتبات", "تكلفة الأجور بالشهر", "أرصدة السلف"])
    df = svc.payroll_register(d1, d2, emp)
    money = {c: MONEY for c in df.columns[4:]}
    with t1:
        if df.empty:
            st.info("مفيش مرتبات معتمدة في الفترة")
        else:
            st.dataframe(df, hide_index=True, width="stretch", column_config=money)
            export_buttons(df, "سجل المرتبات", "payroll_report", "payroll_register", subtitle=f"من {d1} إلى {d2}",
                           totals={c: round(float(df[c].sum()), 2) for c in df.columns[4:]})
    with t2:
        if not df.empty:
            by = df.groupby(["الشهر", "القسم"], as_index=False)[["الأساسي", "الإضافي", "مكافآت", "جزاءات", "المستحق",
                                                                "الصافي"]].sum()
            st.dataframe(by, hide_index=True, width="stretch", column_config={c: MONEY for c in by.columns[2:]})
            pivot = df.pivot_table(index="الشهر", columns="القسم", values="المستحق", aggfunc="sum").fillna(0)
            st.bar_chart(pivot)
            export_buttons(by, "تكلفة الأجور بالشهر", "payroll_report", "payroll_by_month", key="by_month")
    with t3:
        bal = svc.advance_balances(d2)
        st.dataframe(bal, hide_index=True, width="stretch", column_config={c: MONEY for c in bal.columns[1:]})
        export_buttons(bal, "أرصدة سلف الموظفين", "payroll_report", "advance_balances", subtitle=f"في {d2}",
                       key="adv_bal")

