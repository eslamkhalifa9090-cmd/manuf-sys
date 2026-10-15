"""Employees, daily attendance, advances and the monthly payroll, as the workbook's «موظفين» sheet.

Hourly rate = monthly salary / (30 days x the employee's hours a day), or daily wage / hours a day.
Pay = (hours + overtime x the employee's overtime factor - deducted hours) x hourly rate
      + bonuses - penalties - the advance instalment taken this month.
The approved payroll posts Dr wages (5201 / 5202 production, 6101 administration),
Cr employee advances (1602) and Cr accrued wages (2202); salaries are paid by cash voucher from 2202.
"""
from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base
from core.models.costing import Pct
from core.models.documents import DocumentMixin
from core.models.inventory import Qty, UnitCost
from core.models.ledger import Money

PRODUCTION = "production"
ADMINISTRATION = "admin"
DEPARTMENTS = {PRODUCTION: "إنتاج (5201 / 5202)", ADMINISTRATION: "إدارة وبيع (6101)"}

MONTHLY = "monthly"
DAILY = "daily"
PAY_BASES = {MONTHLY: "شهري", DAILY: "باليومية"}

PRESENT = "present"
PAID_LEAVE = "paid_leave"
UNPAID_LEAVE = "unpaid_leave"
ABSENT = "absent"
DAY_STATUSES = {PRESENT: "حضور", PAID_LEAVE: "إجازة مدفوعة", UNPAID_LEAVE: "إجازة بدون أجر", ABSENT: "غياب"}

DAYS_IN_MONTH = 30


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    job: Mapped[str | None] = mapped_column(String(100))
    department: Mapped[str] = mapped_column(String(10), default=PRODUCTION)
    pay_basis: Mapped[str] = mapped_column(String(10), default=MONTHLY)
    monthly_salary: Mapped[float] = mapped_column(Money, default=0)
    daily_wage: Mapped[float] = mapped_column(Money, default=0)
    hours_per_day: Mapped[float] = mapped_column(Qty, default=10)
    overtime_factor: Mapped[float] = mapped_column(Pct, default=1)
    hire_date: Mapped[date | None] = mapped_column(Date)
    phone: Mapped[str | None] = mapped_column(String(40))
    national_id: Mapped[str | None] = mapped_column(String(20))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    @property
    def hourly_rate(self) -> float:
        hours = float(self.hours_per_day or 0)
        if not hours:
            return 0.0
        if self.pay_basis == DAILY:
            return float(self.daily_wage or 0) / hours
        return float(self.monthly_salary or 0) / (DAYS_IN_MONTH * hours)


class Attendance(Base):
    """One day of one employee. Locked once the month's payroll exists."""

    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("employee_id", "work_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), index=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(15), default=PRESENT)
    time_in: Mapped[time | None] = mapped_column(Time)
    time_out: Mapped[time | None] = mapped_column(Time)
    # Paid regular hours (capped at the employee's day), overtime hours and hours deducted (lateness etc.).
    hours: Mapped[float] = mapped_column(Qty, default=0)
    overtime_hours: Mapped[float] = mapped_column(Qty, default=0)
    deduction_hours: Mapped[float] = mapped_column(Qty, default=0)
    bonus: Mapped[float] = mapped_column(Money, default=0)
    penalty: Mapped[float] = mapped_column(Money, default=0)
    note: Mapped[str | None] = mapped_column(String(255))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class EmployeeAdvance(DocumentMixin, Base):
    """Cash advanced to an employee: Dr 1602 / Cr treasury. Recovered through payroll."""

    __tablename__ = "employee_advances"
    PREFIX = "ADV"

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[float] = mapped_column(Money)
    treasury_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    # Monthly instalment the payroll suggests; zero means take it all from the next payroll.
    installment: Mapped[float] = mapped_column(Money, default=0)


class Payroll(DocumentMixin, Base):
    __tablename__ = "payrolls"
    __table_args__ = (UniqueConstraint("year", "month"),)
    PREFIX = "PAY"

    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    lines: Mapped[list["PayrollLine"]] = relationship(back_populates="payroll", cascade="all, delete-orphan",
                                                     order_by="PayrollLine.id")


class PayrollLine(Base):
    __tablename__ = "payroll_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payroll_id: Mapped[int] = mapped_column(ForeignKey("payrolls.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), index=True)
    department: Mapped[str] = mapped_column(String(10))
    days: Mapped[float] = mapped_column(Qty, default=0)
    hours: Mapped[float] = mapped_column(Qty, default=0)
    overtime_hours: Mapped[float] = mapped_column(Qty, default=0)
    deduction_hours: Mapped[float] = mapped_column(Qty, default=0)
    hourly_rate: Mapped[float] = mapped_column(UnitCost, default=0)
    overtime_factor: Mapped[float] = mapped_column(Pct, default=1)
    # From attendance; the payroll can add a month-level bonus or penalty on top.
    bonus: Mapped[float] = mapped_column(Money, default=0)
    penalty: Mapped[float] = mapped_column(Money, default=0)
    advance_deduction: Mapped[float] = mapped_column(Money, default=0)
    note: Mapped[str | None] = mapped_column(String(255))

    payroll: Mapped[Payroll] = relationship(back_populates="lines")

    @property
    def basic(self) -> float:
        return round(float(self.hours or 0) * float(self.hourly_rate or 0), 2)

    @property
    def overtime(self) -> float:
        return round(float(self.overtime_hours or 0) * float(self.hourly_rate or 0) * float(self.overtime_factor or 1), 2)

    @property
    def deduction(self) -> float:
        return round(float(self.deduction_hours or 0) * float(self.hourly_rate or 0), 2)

    @property
    def gross(self) -> float:
        """Earned this month after hour deductions and penalties, before advances."""
        return round(self.basic + self.overtime - self.deduction + float(self.bonus or 0) - float(self.penalty or 0), 2)

    @property
    def net(self) -> float:
        return round(self.gross - float(self.advance_deduction or 0), 2)
