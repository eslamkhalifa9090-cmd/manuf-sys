"""Employees, daily attendance, advances and the monthly payroll."""
from datetime import date, datetime, time, timedelta

import pandas as pd
from sqlalchemy import func, select

from core.db import session_scope
from core.models import Account, User
from core.models.documents import APPROVED
from core.models.payroll import (ABSENT, ADMINISTRATION, DAILY, DAY_STATUSES, DEPARTMENTS, MONTHLY, PAID_LEAVE,
                                 PAY_BASES, PRESENT, PRODUCTION, UNPAID_LEAVE, Attendance, Employee, EmployeeAdvance,
                                 Payroll, PayrollLine)
from core.services import approval, audit
from core.services.approval import PostingLine, r2
from core.services.doc_rules import check_access, check_editable, own_only, resubmit, status_label
from core.services.errors import BusinessError
from core.services.periods import ensure_open
from core.services.statements import month_end

EMPLOYEE_SCREEN = "employees"
ATTENDANCE_SCREEN = "attendance"
ADVANCE_SCREEN = "employee_advances"
PAYROLL_SCREEN = "payroll"
TREASURY_PREFIX = "15"


def _f(x) -> float:
    try:
        v = float(x or 0)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v != v else v


def _key(session, key: str) -> int:
    acc_id = session.scalar(select(Account.id).where(Account.system_key == key))
    if acc_id is None:
        raise BusinessError(f"حساب النظام {key} غير موجود في الشجرة")
    return acc_id


def _names(session):
    return dict(session.execute(select(User.id, User.full_name)).all())


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(int(year), int(month), 1)
    return start, month_end(start)


# ---------------------------------------------------------------- employees

def _next_code(session) -> str:
    n = session.scalar(select(func.count(Employee.id))) or 0
    while True:
        n += 1
        code = f"E-{n:03d}"
        if session.scalar(select(Employee.id).where(Employee.code == code)) is None:
            return code


def save_employee(actor, employee_id: int | None, data: dict) -> int:
    name = (data.get("name") or "").strip()
    if not name:
        raise BusinessError("اسم الموظف مطلوب")
    basis = data.get("pay_basis") or MONTHLY
    if basis not in PAY_BASES:
        raise BusinessError("نظام الأجر غير معروف")
    department = data.get("department") or PRODUCTION
    if department not in DEPARTMENTS:
        raise BusinessError("القسم غير معروف")
    salary, wage = r2(_f(data.get("monthly_salary"))), r2(_f(data.get("daily_wage")))
    hours, factor = _f(data.get("hours_per_day") or 10), _f(data.get("overtime_factor") or 1)
    if basis == MONTHLY and salary <= 0:
        raise BusinessError("اكتب الراتب الشهري")
    if basis == DAILY and wage <= 0:
        raise BusinessError("اكتب اليومية")
    if not 0 < hours <= 24:
        raise BusinessError("ساعات اليوم لازم تكون بين 1 و 24")
    if factor <= 0:
        raise BusinessError("معامل الإضافي لازم يكون أكبر من صفر")
    with session_scope() as s:
        if employee_id is None:
            if not actor.can(EMPLOYEE_SCREEN, "add"):
                raise BusinessError("ليس لديك صلاحية الإضافة")
            e = Employee(code=(data.get("code") or "").strip() or _next_code(s))
        else:
            if not actor.can(EMPLOYEE_SCREEN, "edit"):
                raise BusinessError("ليس لديك صلاحية التعديل")
            e = s.get(Employee, employee_id)
            if e is None:
                raise BusinessError("الموظف غير موجود")
            if data.get("code"):
                e.code = data["code"].strip()
        for field, msg in (("name", "اسم الموظف مستخدم قبل كده"), ("code", "الكود مستخدم قبل كده")):
            val = name if field == "name" else e.code
            dup = s.scalar(select(Employee.id).where(getattr(Employee, field) == val, Employee.id != (employee_id or 0)))
            if dup:
                raise BusinessError(msg)
        e.name, e.job, e.department, e.pay_basis = name, (data.get("job") or "").strip() or None, department, basis
        e.monthly_salary, e.daily_wage = salary if basis == MONTHLY else 0, wage if basis == DAILY else 0
        e.hours_per_day, e.overtime_factor = hours, factor
        e.hire_date = data.get("hire_date")
        e.phone = (data.get("phone") or "").strip() or None
        e.national_id = (data.get("national_id") or "").strip() or None
        e.notes = (data.get("notes") or "").strip() or None
        e.is_active = bool(data.get("is_active", True))
        s.add(e)
        s.flush()
        audit.log(s, actor, "create" if employee_id is None else "update", "employee", e.id,
                  {"code": e.code, "name": e.name})
        return e.id


def delete_employee(actor, employee_id: int) -> None:
    if not actor.can(EMPLOYEE_SCREEN, "delete"):
        raise BusinessError("ليس لديك صلاحية الحذف")
    with session_scope() as s:
        e = s.get(Employee, employee_id)
        if e is None:
            return
        for q in (select(Attendance.id).where(Attendance.employee_id == employee_id),
                  select(EmployeeAdvance.id).where(EmployeeAdvance.employee_id == employee_id),
                  select(PayrollLine.id).where(PayrollLine.employee_id == employee_id)):
            if s.scalar(q.limit(1)) is not None:
                raise BusinessError("الموظف عليه حضور أو سلف أو مرتبات؛ أوقفه بدل الحذف")
        audit.log(s, actor, "delete", "employee", e.id, {"code": e.code, "name": e.name})
        s.delete(e)


def get_employee(employee_id: int) -> dict:
    with session_scope() as s:
        e = s.get(Employee, employee_id)
        if e is None:
            raise BusinessError("الموظف غير موجود")
        return {c: getattr(e, c) for c in ("id", "code", "name", "job", "department", "pay_basis", "hire_date", "phone",
                                           "national_id", "notes", "is_active")} | {
            "monthly_salary": float(e.monthly_salary or 0), "daily_wage": float(e.daily_wage or 0),
            "hours_per_day": float(e.hours_per_day or 10), "overtime_factor": float(e.overtime_factor or 1),
            "hourly_rate": e.hourly_rate}


def employee_options(active_only: bool = True) -> dict[int, str]:
    with session_scope() as s:
        q = select(Employee).order_by(Employee.name)
        if active_only:
            q = q.where(Employee.is_active)
        return {e.id: f"{e.code} - {e.name}" for e in s.scalars(q)}


def employees_frame(include_inactive: bool = True) -> pd.DataFrame:
    with session_scope() as s:
        q = select(Employee).order_by(Employee.code)
        if not include_inactive:
            q = q.where(Employee.is_active)
        rows = [{"id": e.id, "الكود": e.code, "الاسم": e.name, "الوظيفة": e.job or "",
                 "القسم": DEPARTMENTS[e.department], "نظام الأجر": PAY_BASES[e.pay_basis],
                 "الراتب / اليومية": float(e.monthly_salary if e.pay_basis == MONTHLY else e.daily_wage),
                 "ساعات اليوم": float(e.hours_per_day), "معامل الإضافي": float(e.overtime_factor),
                 "قيمة الساعة": round(e.hourly_rate, 4), "نشط": e.is_active} for e in s.scalars(q)]
    return pd.DataFrame(rows, columns=["id", "الكود", "الاسم", "الوظيفة", "القسم", "نظام الأجر", "الراتب / اليومية",
                                       "ساعات اليوم", "معامل الإضافي", "قيمة الساعة", "نشط"])


# ---------------------------------------------------------------- attendance

def _payroll_for(session, d: date) -> Payroll | None:
    return session.scalar(select(Payroll).where(Payroll.year == d.year, Payroll.month == d.month))


def _check_unlocked(session, d: date) -> None:
    p = _payroll_for(session, d)
    if p is not None:
        raise BusinessError(f"مرتبات شهر {d.month}/{d.year} اتحسبت ({p.doc_no}); احذفها أو الغي اعتمادها الأول")


def _as_time(v) -> time | None:
    if v is None or v == "" or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, time):
        return v
    if isinstance(v, datetime):
        return v.time()
    if hasattr(v, "to_pytimedelta"):
        v = v.to_pytimedelta()
    if isinstance(v, timedelta):
        secs = int(v.total_seconds()) % 86400
        return time(secs // 3600, secs % 3600 // 60)
    if isinstance(v, (int, float)):  # an Excel fraction of a day
        mins = round(float(v) * 1440) % 1440
        return time(mins // 60, mins % 60)
    text = str(v).strip()
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise BusinessError(f"وقت غير مفهوم: {text} (اكتبه كده 10:30 أو 20:15)")


def worked_hours(time_in: time | None, time_out: time | None) -> float | None:
    if time_in is None or time_out is None:
        return None
    a = time_in.hour * 60 + time_in.minute
    b = time_out.hour * 60 + time_out.minute
    if b < a:  # a night shift past midnight
        b += 1440
    return round((b - a) / 60, 4)


def day_figures(e: Employee, status: str, time_in, time_out, overtime=None) -> tuple[float, float]:
    """(paid regular hours, overtime hours) for a day, as the workbook: hours capped at the employee's day."""
    day = float(e.hours_per_day or 10)
    if status == PAID_LEAVE:
        return day, 0.0
    if status in (UNPAID_LEAVE, ABSENT):
        return 0.0, 0.0
    worked = worked_hours(time_in, time_out)
    if worked is None:
        worked = day
    regular = min(worked, day)
    extra = max(worked - day, 0.0) if overtime is None else _f(overtime)
    return round(regular, 4), round(extra, 4)


def save_day(actor, work_date: date, rows: list[dict]) -> int:
    """rows: employee_id, status, time_in, time_out, overtime_hours (None = from the times), deduction_hours,
    bonus, penalty, note. A row with no status clears that employee's day."""
    if work_date is None:
        raise BusinessError("التاريخ مطلوب")
    with session_scope() as s:
        ensure_open(s, work_date)
        _check_unlocked(s, work_date)
        existing = {a.employee_id: a for a in s.scalars(select(Attendance).where(Attendance.work_date == work_date))}
        saved = 0
        for r in rows:
            e = s.get(Employee, r.get("employee_id"))
            if e is None:
                continue
            status = r.get("status")
            att = existing.get(e.id)
            if not status:
                if att is not None:
                    if not actor.can(ATTENDANCE_SCREEN, "delete"):
                        raise BusinessError("ليس لديك صلاحية الحذف")
                    s.delete(att)
                continue
            if status not in DAY_STATUSES:
                raise BusinessError(f"{e.name}: حالة اليوم غير معروفة")
            if not actor.can(ATTENDANCE_SCREEN, "edit" if att else "add"):
                raise BusinessError("ليس لديك صلاحية لهذه العملية")
            t_in, t_out = _as_time(r.get("time_in")), _as_time(r.get("time_out"))
            if status == PRESENT and bool(t_in) != bool(t_out):
                raise BusinessError(f"{e.name}: اكتب الحضور والانصراف الاتنين أو سيبهم فاضيين (يوم كامل)")
            ot = r.get("overtime_hours")
            ot = None if ot is None or (isinstance(ot, float) and ot != ot) or ot == "" else ot
            hours, extra = day_figures(e, status, t_in, t_out, ot)
            ded, bonus, penalty = _f(r.get("deduction_hours")), r2(_f(r.get("bonus"))), r2(_f(r.get("penalty")))
            if min(extra, ded, bonus, penalty) < 0:
                raise BusinessError(f"{e.name}: مينفعش قيم بالسالب")
            if att is None:
                att = Attendance(employee_id=e.id, work_date=work_date, created_by_id=actor.id)
                s.add(att)
            att.status, att.time_in, att.time_out = status, t_in if status == PRESENT else None, \
                t_out if status == PRESENT else None
            att.hours, att.overtime_hours, att.deduction_hours = hours, extra, ded
            att.bonus, att.penalty = bonus, penalty
            att.note = (r.get("note") or "").strip() or None if isinstance(r.get("note"), str) else None
            saved += 1
        audit.log(s, actor, "save", "attendance", 0, {"date": work_date.isoformat(), "rows": saved})
        return saved


def fill_rest_days(actor, year: int, month: int, weekday: int = 4) -> int:
    """Record the weekly day off (Friday by default) as paid leave for every active employee with nothing that day.

    The hourly rate divides the salary over 30 days, so the paid day off is what brings a full month to the salary.
    """
    start, end = _month_bounds(int(year), int(month))
    added = 0
    with session_scope() as s:
        ensure_open(s, end)
        _check_unlocked(s, start)
        if not actor.can(ATTENDANCE_SCREEN, "add"):
            raise BusinessError("ليس لديك صلاحية الإضافة")
        emps = list(s.scalars(select(Employee).where(Employee.is_active)))
        taken = {(a.employee_id, a.work_date) for a in s.scalars(
            select(Attendance).where(Attendance.work_date >= start, Attendance.work_date <= end))}
        d = start
        while d <= end:
            if d.weekday() == weekday:
                for e in emps:
                    if (e.id, d) not in taken and (e.hire_date is None or e.hire_date <= d):
                        s.add(Attendance(employee_id=e.id, work_date=d, status=PAID_LEAVE,
                                         hours=float(e.hours_per_day or 10), created_by_id=actor.id))
                        added += 1
            d += timedelta(days=1)
        audit.log(s, actor, "save", "attendance", 0, {"rest_days": f"{month}/{year}", "rows": added})
    return added


def day_frame(work_date: date) -> list[dict]:
    """Every active employee (and anyone with a record that day) with the day's entry, for the day grid."""
    with session_scope() as s:
        recs = {a.employee_id: a for a in s.scalars(select(Attendance).where(Attendance.work_date == work_date))}
        emps = [e for e in s.scalars(select(Employee).order_by(Employee.code)) if e.is_active or e.id in recs]
        out = []
        for e in emps:
            a = recs.get(e.id)
            out.append({"employee_id": e.id, "name": f"{e.code} - {e.name}", "status": a.status if a else None,
                        "time_in": a.time_in if a else None, "time_out": a.time_out if a else None,
                        "hours": float(a.hours) if a else None,
                        "overtime_hours": float(a.overtime_hours) if a else None,
                        "deduction_hours": float(a.deduction_hours) if a else 0.0,
                        "bonus": float(a.bonus) if a else 0.0, "penalty": float(a.penalty) if a else 0.0,
                        "note": (a.note or "") if a else ""})
        return out


def attendance_frame(year: int, month: int, employee_id: int | None = None) -> pd.DataFrame:
    start, end = _month_bounds(year, month)
    with session_scope() as s:
        emps = {e.id: e for e in s.scalars(select(Employee))}
        q = (select(Attendance).where(Attendance.work_date >= start, Attendance.work_date <= end)
             .order_by(Attendance.work_date, Attendance.employee_id))
        if employee_id:
            q = q.where(Attendance.employee_id == employee_id)
        rows = [{"التاريخ": a.work_date, "الموظف": emps[a.employee_id].name, "الحالة": DAY_STATUSES[a.status],
                 "الحضور": a.time_in.strftime("%H:%M") if a.time_in else "",
                 "الانصراف": a.time_out.strftime("%H:%M") if a.time_out else "", "الساعات": float(a.hours),
                 "إضافي": float(a.overtime_hours), "خصم ساعات": float(a.deduction_hours), "مكافأة": float(a.bonus),
                 "جزاء": float(a.penalty), "ملاحظة": a.note or ""} for a in s.scalars(q)]
    return pd.DataFrame(rows, columns=["التاريخ", "الموظف", "الحالة", "الحضور", "الانصراف", "الساعات", "إضافي",
                                       "خصم ساعات", "مكافأة", "جزاء", "ملاحظة"])


def attendance_summary(year: int, month: int) -> pd.DataFrame:
    start, end = _month_bounds(year, month)
    with session_scope() as s:
        emps = {e.id: e for e in s.scalars(select(Employee))}
        acc: dict[int, dict] = {}
        for a in s.scalars(select(Attendance).where(Attendance.work_date >= start, Attendance.work_date <= end)):
            r = acc.setdefault(a.employee_id, {"present": 0, "paid": 0, "unpaid": 0, "absent": 0, "hours": 0.0,
                                               "ot": 0.0, "ded": 0.0, "bonus": 0.0, "penalty": 0.0})
            r[{PRESENT: "present", PAID_LEAVE: "paid", UNPAID_LEAVE: "unpaid", ABSENT: "absent"}[a.status]] += 1
            r["hours"] += float(a.hours)
            r["ot"] += float(a.overtime_hours)
            r["ded"] += float(a.deduction_hours)
            r["bonus"] += float(a.bonus)
            r["penalty"] += float(a.penalty)
    rows = [{"الموظف": emps[i].name, "أيام حضور": r["present"], "إجازة مدفوعة": r["paid"], "إجازة بدون أجر": r["unpaid"],
             "غياب": r["absent"], "الساعات": round(r["hours"], 2), "إضافي": round(r["ot"], 2),
             "خصم ساعات": round(r["ded"], 2), "مكافآت": r2(r["bonus"]), "جزاءات": r2(r["penalty"]),
             "أيام بـ ساعات اليوم": round(r["hours"] / float(emps[i].hours_per_day or 10), 2)}
            for i, r in sorted(acc.items(), key=lambda kv: emps[kv[0]].code)]
    return pd.DataFrame(rows, columns=["الموظف", "أيام حضور", "إجازة مدفوعة", "إجازة بدون أجر", "غياب", "الساعات", "إضافي",
                                       "خصم ساعات", "مكافآت", "جزاءات", "أيام بـ ساعات اليوم"])


IMPORT_COLUMNS = ["الموظف", "التاريخ", "الحالة", "الحضور", "الانصراف", "ساعات إضافي", "خصم ساعات", "مكافأة", "جزاء",
                  "ملاحظة"]
STATUS_BY_LABEL = {v: k for k, v in DAY_STATUSES.items()}


def import_template() -> pd.DataFrame:
    return pd.DataFrame([{"الموظف": "E-001", "التاريخ": date.today().isoformat(), "الحالة": DAY_STATUSES[PRESENT],
                          "الحضور": "10:00", "الانصراف": "20:30", "ساعات إضافي": None, "خصم ساعات": 0, "مكافأة": 0,
                          "جزاء": 0, "ملاحظة": ""}], columns=IMPORT_COLUMNS)


def _parse_rows(df: pd.DataFrame) -> list[dict]:
    with session_scope() as s:
        by_code = {e.code: e.id for e in s.scalars(select(Employee))}
        by_name = {e.name: e.id for e in s.scalars(select(Employee))}
    out = []
    for i, r in df.iterrows():
        row = {"row": i + 1, "error": ""}
        key = str(r.get("الموظف") or "").strip()
        row["employee_id"] = by_code.get(key) or by_name.get(key)
        try:
            row["date"] = pd.to_datetime(r.get("التاريخ")).date()
        except Exception:
            row["date"] = None
        label = str(r.get("الحالة") or "").strip() or DAY_STATUSES[PRESENT]
        row["status"] = STATUS_BY_LABEL.get(label)
        try:
            row["time_in"], row["time_out"] = _as_time(r.get("الحضور")), _as_time(r.get("الانصراف"))
        except BusinessError as exc:
            row["error"] = str(exc)
        ot = r.get("ساعات إضافي")
        row["overtime_hours"] = None if ot is None or (isinstance(ot, float) and ot != ot) or ot == "" else _f(ot)
        row["deduction_hours"], row["bonus"], row["penalty"] = _f(r.get("خصم ساعات")), _f(r.get("مكافأة")), _f(r.get("جزاء"))
        note = r.get("ملاحظة")
        row["note"] = note if isinstance(note, str) else ""
        if not row["error"]:
            if row["employee_id"] is None:
                row["error"] = "الموظف غير موجود (اكتب الكود أو الاسم)"
            elif row["date"] is None:
                row["error"] = "التاريخ غير صحيح"
            elif row["status"] is None:
                row["error"] = "الحالة لازم تكون: " + " / ".join(DAY_STATUSES.values())
        out.append(row)
    return out


def validate_import(df: pd.DataFrame) -> pd.DataFrame:
    rows = _parse_rows(df)
    with session_scope() as s:
        for r in rows:
            if not r["error"] and _payroll_for(s, r["date"]) is not None:
                r["error"] = "مرتبات الشهر ده اتحسبت"
    out = df.copy()
    out["الحالة"] = ["جاهز" if not r["error"] else "مرفوض" for r in rows]
    out["السبب"] = [r["error"] for r in rows]
    return out


def import_attendance(actor, df: pd.DataFrame) -> int:
    rows = [r for r in _parse_rows(df) if not r["error"]]
    by_day: dict[date, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(r)
    total = 0
    for d, items in sorted(by_day.items()):
        total += save_day(actor, d, items)
    return total


# ---------------------------------------------------------------- advances

def save_advance(actor, advance_id: int | None, doc_date: date, employee_id: int, amount: float,
                 treasury_account_id: int, installment: float = 0, memo: str = "") -> int:
    amount, installment = r2(_f(amount)), r2(_f(installment))
    if doc_date is None:
        raise BusinessError("التاريخ مطلوب")
    if amount <= 0:
        raise BusinessError("المبلغ لازم يكون أكبر من صفر")
    if installment < 0 or installment > amount:
        raise BusinessError("القسط الشهري لازم يكون بين صفر وقيمة السلفة")
    with session_scope() as s:
        e = s.get(Employee, employee_id) if employee_id else None
        if e is None:
            raise BusinessError("اختار الموظف")
        t = s.get(Account, treasury_account_id) if treasury_account_id else None
        if t is None or not t.code.startswith(TREASURY_PREFIX) or not t.is_postable:
            raise BusinessError("اختار الخزينة أو المحفظة أو البنك")
        if advance_id is None:
            check_access(actor, ADVANCE_SCREEN, "add")
            doc = EmployeeAdvance(created_by_id=actor.id)
        else:
            doc = s.get(EmployeeAdvance, advance_id)
            if doc is None:
                raise BusinessError("المستند غير موجود")
            check_access(actor, ADVANCE_SCREEN, "edit", doc)
            check_editable(doc)
            resubmit(doc)
        ensure_open(s, doc_date)
        doc.doc_date, doc.employee_id, doc.amount, doc.installment = doc_date, employee_id, amount, installment
        doc.treasury_account_id = treasury_account_id
        doc.memo = (memo or "").strip() or f"سلفة {e.name}"
        s.add(doc)
        s.flush()
        audit.log(s, actor, "create" if advance_id is None else "update", "employee_advance", doc.id,
                  {"doc_no": doc.doc_no, "amount": amount})
        return doc.id


def delete_advance(actor, advance_id: int) -> None:
    with session_scope() as s:
        doc = s.get(EmployeeAdvance, advance_id)
        if doc is None:
            return
        check_access(actor, ADVANCE_SCREEN, "delete", doc)
        check_editable(doc)
        audit.log(s, actor, "delete", "employee_advance", doc.id, {"doc_no": doc.doc_no})
        s.delete(doc)


def get_advance(advance_id: int) -> dict:
    with session_scope() as s:
        d = s.get(EmployeeAdvance, advance_id)
        if d is None:
            raise BusinessError("المستند غير موجود")
        return {"id": d.id, "doc_no": d.doc_no, "doc_date": d.doc_date, "employee_id": d.employee_id,
                "amount": float(d.amount), "installment": float(d.installment or 0),
                "treasury_account_id": d.treasury_account_id, "memo": d.memo or "", "status": d.status,
                "rejection_note": d.rejection_note}


def _advances(session, employee_id: int, as_of: date | None = None, exclude_payroll: int | None = None):
    """Approved advances with what is left of each, oldest first (deductions are taken oldest first)."""
    q = select(EmployeeAdvance).where(EmployeeAdvance.employee_id == employee_id, EmployeeAdvance.status == APPROVED)
    if as_of:
        q = q.where(EmployeeAdvance.doc_date <= as_of)
    advances = list(session.scalars(q.order_by(EmployeeAdvance.doc_date, EmployeeAdvance.id)))
    dq = (select(func.sum(PayrollLine.advance_deduction)).join(Payroll)
          .where(PayrollLine.employee_id == employee_id, Payroll.status == APPROVED))
    if exclude_payroll:
        dq = dq.where(Payroll.id != exclude_payroll)
    taken = float(session.scalar(dq) or 0)
    out = []
    for a in advances:
        use = min(taken, float(a.amount))
        taken -= use
        out.append((a, r2(float(a.amount) - use)))
    return out


def advance_balance(session, employee_id: int, as_of: date | None = None, exclude_payroll: int | None = None) -> float:
    return r2(sum(left for _, left in _advances(session, employee_id, as_of, exclude_payroll)))


def suggested_deduction(session, employee_id: int, as_of: date, exclude_payroll: int | None = None) -> float:
    total = 0.0
    for a, left in _advances(session, employee_id, as_of, exclude_payroll):
        if left > 0:
            inst = float(a.installment or 0)
            total += left if not inst else min(inst, left)
    return r2(total)


def advances_frame(actor) -> pd.DataFrame:
    with session_scope() as s:
        names, emps = _names(s), {e.id: e.name for e in s.scalars(select(Employee))}
        q = select(EmployeeAdvance).order_by(EmployeeAdvance.doc_date.desc(), EmployeeAdvance.id.desc())
        if own_only(actor, ADVANCE_SCREEN):
            q = q.where(EmployeeAdvance.created_by_id == actor.id)
        rows = [{"id": d.id, "رقم المستند": d.doc_no, "التاريخ": d.doc_date, "الموظف": emps.get(d.employee_id, ""),
                 "المبلغ": float(d.amount), "القسط الشهري": f"{float(d.installment):,.2f}" if d.installment else "كله مرة واحدة",
                 "الحالة": status_label(d.status), "ملاحظة المراجع": d.rejection_note or "",
                 "سجّله": names.get(d.created_by_id, "")} for d in s.scalars(q)]
    return pd.DataFrame(rows, columns=["id", "رقم المستند", "التاريخ", "الموظف", "المبلغ", "القسط الشهري", "الحالة",
                                       "ملاحظة المراجع", "سجّله"])


def advance_balances(as_of: date | None = None) -> pd.DataFrame:
    with session_scope() as s:
        rows = []
        for e in s.scalars(select(Employee).order_by(Employee.code)):
            advs = _advances(s, e.id, as_of)
            given = r2(sum(float(a.amount) for a, _ in advs))
            left = r2(sum(l for _, l in advs))
            if given:
                rows.append({"الموظف": f"{e.code} - {e.name}", "إجمالي السلف": given, "اتخصم": r2(given - left),
                             "الرصيد": left})
    return pd.DataFrame(rows, columns=["الموظف", "إجمالي السلف", "اتخصم", "الرصيد"])


def _adv_lines(session, doc: EmployeeAdvance) -> list[PostingLine]:
    return [PostingLine(_key(session, "employee_advances"), debit=float(doc.amount), memo=doc.memo),
            PostingLine(doc.treasury_account_id, credit=float(doc.amount), memo=doc.memo)]


def _adv_void(session, doc: EmployeeAdvance) -> None:
    """Refuse when approved payrolls already recovered more than the other advances cover."""
    taken = float(session.scalar(select(func.sum(PayrollLine.advance_deduction)).join(Payroll).where(
        PayrollLine.employee_id == doc.employee_id, Payroll.status == APPROVED)) or 0)
    given = sum(float(a.amount) for a in session.scalars(select(EmployeeAdvance).where(
        EmployeeAdvance.employee_id == doc.employee_id, EmployeeAdvance.status == APPROVED,
        EmployeeAdvance.id != doc.id)))
    if taken > given + 0.005:
        raise BusinessError("السلفة دي اتخصم منها في مرتبات معتمدة؛ الغي اعتماد المرتبات الأول")


def _adv_describe(session, doc: EmployeeAdvance):
    e = session.get(Employee, doc.employee_id)
    return "سلفة موظف", e.name if e else "", float(doc.amount)


approval.register(approval.DocType("employee_advance", EmployeeAdvance, ADVANCE_SCREEN, _adv_describe, _adv_lines,
                                   on_void=_adv_void))


# ---------------------------------------------------------------- payroll

def _month_lines(session, year: int, month: int, exclude_payroll: int | None = None) -> list[PayrollLine]:
    start, end = _month_bounds(year, month)
    recs: dict[int, list[Attendance]] = {}
    for a in session.scalars(select(Attendance).where(Attendance.work_date >= start, Attendance.work_date <= end)):
        recs.setdefault(a.employee_id, []).append(a)
    lines = []
    for e in session.scalars(select(Employee).order_by(Employee.code)):
        days = recs.get(e.id, [])
        if not days:
            continue
        line = PayrollLine(employee_id=e.id, department=e.department, hourly_rate=round(e.hourly_rate, 6),
                           overtime_factor=float(e.overtime_factor or 1),
                           days=sum(1 for a in days if float(a.hours) > 0),
                           hours=round(sum(float(a.hours) for a in days), 4),
                           overtime_hours=round(sum(float(a.overtime_hours) for a in days), 4),
                           deduction_hours=round(sum(float(a.deduction_hours) for a in days), 4),
                           bonus=r2(sum(float(a.bonus) for a in days)),
                           penalty=r2(sum(float(a.penalty) for a in days)))
        earned = max(line.gross, 0.0)
        line.advance_deduction = min(suggested_deduction(session, e.id, end, exclude_payroll), earned)
        lines.append(line)
    return lines


def compute_payroll(actor, year: int, month: int, memo: str = "") -> int:
    year, month = int(year), int(month)
    if not 1 <= month <= 12:
        raise BusinessError("الشهر غير صحيح")
    start, end = _month_bounds(year, month)
    with session_scope() as s:
        ensure_open(s, end)
        doc = s.scalar(select(Payroll).where(Payroll.year == year, Payroll.month == month))
        if doc is None:
            check_access(actor, PAYROLL_SCREEN, "add")
        else:
            check_access(actor, PAYROLL_SCREEN, "edit", doc)
            check_editable(doc)
        lines = _month_lines(s, year, month, doc.id if doc else None)
        if not lines:
            raise BusinessError("مفيش حضور مسجل في الشهر ده")
        if doc is None:
            doc = Payroll(created_by_id=actor.id, year=year, month=month)
            s.add(doc)
        else:
            resubmit(doc)
        doc.doc_date, doc.memo = end, (memo or "").strip() or f"مرتبات شهر {month}/{year}"
        doc.lines = lines
        s.flush()
        audit.log(s, actor, "compute", "payroll", doc.id, {"doc_no": doc.doc_no, "employees": len(lines)})
        return doc.id


def update_line(actor, payroll_id: int, employee_id: int, bonus: float, penalty: float, advance_deduction: float,
                note: str = "") -> None:
    """Month-level changes to one employee's line on a payroll not yet approved."""
    with session_scope() as s:
        doc = s.get(Payroll, payroll_id)
        if doc is None:
            raise BusinessError("المستند غير موجود")
        check_access(actor, PAYROLL_SCREEN, "edit", doc)
        check_editable(doc)
        line = next((l for l in doc.lines if l.employee_id == employee_id), None)
        if line is None:
            raise BusinessError("الموظف مش في الكشف")
        bonus, penalty, adv = r2(_f(bonus)), r2(_f(penalty)), r2(_f(advance_deduction))
        if min(bonus, penalty, adv) < 0:
            raise BusinessError("مينفعش قيم بالسالب")
        balance = advance_balance(s, employee_id, doc.doc_date, exclude_payroll=doc.id)
        if adv > balance + 0.005:
            raise BusinessError(f"خصم السلفة أكبر من رصيدها ({balance:,.2f})")
        line.bonus, line.penalty, line.advance_deduction = bonus, penalty, adv
        line.note = (note or "").strip() or None
        if line.net < 0:
            raise BusinessError("الصافي بقى بالسالب؛ قلل الجزاء أو خصم السلفة")
        resubmit(doc)
        audit.log(s, actor, "update", "payroll", doc.id, {"employee": employee_id, "net": line.net})


def delete_payroll(actor, payroll_id: int) -> None:
    with session_scope() as s:
        doc = s.get(Payroll, payroll_id)
        if doc is None:
            return
        check_access(actor, PAYROLL_SCREEN, "delete", doc)
        check_editable(doc)
        audit.log(s, actor, "delete", "payroll", doc.id, {"doc_no": doc.doc_no})
        s.delete(doc)


def payrolls_frame(actor) -> pd.DataFrame:
    with session_scope() as s:
        names = _names(s)
        q = select(Payroll).order_by(Payroll.year.desc(), Payroll.month.desc())
        if own_only(actor, PAYROLL_SCREEN):
            q = q.where(Payroll.created_by_id == actor.id)
        rows = [{"id": d.id, "رقم المستند": d.doc_no, "الشهر": f"{d.month:02d}/{d.year}", "عدد الموظفين": len(d.lines),
                 "الإجمالي": r2(sum(l.gross for l in d.lines)),
                 "خصم السلف": r2(sum(float(l.advance_deduction or 0) for l in d.lines)),
                 "الصافي": r2(sum(l.net for l in d.lines)), "الحالة": status_label(d.status),
                 "ملاحظة المراجع": d.rejection_note or "", "سجّله": names.get(d.created_by_id, "")}
                for d in s.scalars(q)]
    return pd.DataFrame(rows, columns=["id", "رقم المستند", "الشهر", "عدد الموظفين", "الإجمالي", "خصم السلف", "الصافي",
                                       "الحالة", "ملاحظة المراجع", "سجّله"])


def payroll_sheet(payroll_id: int) -> pd.DataFrame:
    with session_scope() as s:
        doc = s.get(Payroll, payroll_id)
        if doc is None:
            raise BusinessError("المستند غير موجود")
        emps = {e.id: e for e in s.scalars(select(Employee))}
        rows = [{"employee_id": l.employee_id, "الموظف": emps[l.employee_id].name,
                 "القسم": DEPARTMENTS[l.department].split(" (")[0], "أيام": float(l.days), "الساعات": float(l.hours),
                 "قيمة الساعة": round(float(l.hourly_rate), 4), "الأساسي": l.basic,
                 "ساعات إضافي": float(l.overtime_hours), "قيمة الإضافي": l.overtime,
                 "خصم ساعات": float(l.deduction_hours), "قيمة الخصم": l.deduction, "مكافآت": float(l.bonus),
                 "جزاءات": float(l.penalty), "المستحق": l.gross, "خصم السلف": float(l.advance_deduction),
                 "الصافي": l.net, "ملاحظة": l.note or ""} for l in doc.lines]
    return pd.DataFrame(rows, columns=["employee_id", "الموظف", "القسم", "أيام", "الساعات", "قيمة الساعة", "الأساسي",
                                       "ساعات إضافي", "قيمة الإضافي", "خصم ساعات", "قيمة الخصم", "مكافآت", "جزاءات",
                                       "المستحق", "خصم السلف", "الصافي", "ملاحظة"])


def payroll_register(date_from: date, date_to: date, employee_id: int | None = None) -> pd.DataFrame:
    """Approved payroll lines per month and employee, for the payroll reports."""
    with session_scope() as s:
        emps = {e.id: e for e in s.scalars(select(Employee))}
        q = (select(Payroll).where(Payroll.status == APPROVED, Payroll.doc_date >= date_from,
                                   Payroll.doc_date <= date_to).order_by(Payroll.year, Payroll.month))
        rows = []
        for d in s.scalars(q):
            for l in d.lines:
                if employee_id and l.employee_id != employee_id:
                    continue
                rows.append({"الشهر": f"{d.year}-{d.month:02d}", "الموظف": emps[l.employee_id].name,
                             "القسم": DEPARTMENTS[l.department].split(" (")[0], "الساعات": float(l.hours),
                             "الأساسي": l.basic, "الإضافي": l.overtime, "خصم ساعات": l.deduction,
                             "مكافآت": float(l.bonus), "جزاءات": float(l.penalty), "المستحق": l.gross,
                             "خصم السلف": float(l.advance_deduction), "الصافي": l.net})
    return pd.DataFrame(rows, columns=["الشهر", "الموظف", "القسم", "الساعات", "الأساسي", "الإضافي", "خصم ساعات", "مكافآت",
                                       "جزاءات", "المستحق", "خصم السلف", "الصافي"])


def payslip(payroll_id: int, employee_id: int) -> pd.DataFrame:
    with session_scope() as s:
        doc = s.get(Payroll, payroll_id)
        line = next((l for l in doc.lines if l.employee_id == employee_id), None)
        if line is None:
            raise BusinessError("الموظف مش في الكشف")
        e = s.get(Employee, employee_id)
        balance_after = advance_balance(s, employee_id, doc.doc_date, exclude_payroll=doc.id) - float(
            line.advance_deduction or 0)
        items = [("الموظف", f"{e.code} - {e.name}"), ("الشهر", f"{doc.month:02d}/{doc.year}"),
                 ("نظام الأجر", PAY_BASES[e.pay_basis]),
                 ("الراتب الشهري" if e.pay_basis == MONTHLY else "اليومية",
                  f"{float(e.monthly_salary if e.pay_basis == MONTHLY else e.daily_wage):,.2f}"),
                 ("قيمة الساعة", f"{float(line.hourly_rate):,.4f}"), ("أيام العمل", f"{float(line.days):g}"),
                 ("ساعات العمل", f"{float(line.hours):,.2f}"), ("الأساسي", f"{line.basic:,.2f}"),
                 (f"الإضافي ({float(line.overtime_hours):,.2f} ساعة × {float(line.overtime_factor):g})",
                  f"{line.overtime:,.2f}"),
                 (f"خصم ساعات ({float(line.deduction_hours):,.2f} ساعة)", f"-{line.deduction:,.2f}"),
                 ("مكافآت وحوافز", f"{float(line.bonus):,.2f}"), ("جزاءات", f"-{float(line.penalty):,.2f}"),
                 ("المستحق", f"{line.gross:,.2f}"), ("خصم السلف", f"-{float(line.advance_deduction):,.2f}"),
                 ("الصافي", f"{line.net:,.2f}"), ("رصيد السلف بعد الخصم", f"{r2(balance_after):,.2f}")]
    return pd.DataFrame(items, columns=["البند", "القيمة"])


def _payroll_lines(session, doc: Payroll) -> list[PostingLine]:
    memo = doc.memo
    wages, extra, admin = _key(session, "direct_wages"), _key(session, "production_incentives"), _key(session,
                                                                                                      "admin_salaries")
    totals: dict[int, float] = {}
    for l in doc.lines:
        if l.department == ADMINISTRATION:
            totals[admin] = totals.get(admin, 0.0) + l.gross
        else:
            ex = l.overtime + float(l.bonus or 0)
            totals[extra] = totals.get(extra, 0.0) + ex
            totals[wages] = totals.get(wages, 0.0) + l.gross - ex
    out = []
    for acc, v in totals.items():
        v = r2(v)
        if v:
            out.append(PostingLine(acc, debit=v if v > 0 else 0, credit=-v if v < 0 else 0, memo=memo))
    adv = r2(sum(float(l.advance_deduction or 0) for l in doc.lines))
    if adv:
        out.append(PostingLine(_key(session, "employee_advances"), credit=adv, memo=f"خصم سلف {memo}"))
    net = r2(sum(l.net for l in doc.lines))
    if net:
        out.append(PostingLine(_key(session, "accrued_wages"), credit=net, memo=f"صافي {memo}"))
    return out


def _payroll_post(session, doc: Payroll, entry_id) -> None:
    for l in doc.lines:
        if l.net < 0:
            e = session.get(Employee, l.employee_id)
            raise BusinessError(f"صافي {e.name} بالسالب")
        balance = advance_balance(session, l.employee_id, doc.doc_date, exclude_payroll=doc.id)
        if float(l.advance_deduction or 0) > balance + 0.005:
            e = session.get(Employee, l.employee_id)
            raise BusinessError(f"خصم سلفة {e.name} أكبر من رصيدها ({balance:,.2f}); احسب الكشف تاني")


def _payroll_describe(session, doc: Payroll):
    return "كشف المرتبات", f"شهر {doc.month}/{doc.year} - {len(doc.lines)} موظف", r2(sum(l.gross for l in doc.lines))


approval.register(approval.DocType("payroll", Payroll, PAYROLL_SCREEN, _payroll_describe, _payroll_lines,
                                   on_post=_payroll_post))
