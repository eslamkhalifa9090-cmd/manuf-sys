from datetime import date

import pytest
from sqlalchemy import select

from core.db import session_scope
from core.models import Account
from core.services import approval, checks, fixed_assets, journals, ledger, payroll, statements
from core.services.errors import BusinessError

D = date(2026, 4, 1)


def acc(key_or_code: str) -> int:
    with session_scope() as s:
        return s.scalar(select(Account.id).where((Account.system_key == key_or_code) | (Account.code == key_or_code)))


def bal(code: str) -> float:
    with session_scope() as s:
        dr, cr = ledger._sums(s).get(acc(code), (0, 0))
    return round(dr - cr, 2)


def line(key, dr=0, cr=0):
    return {"account_id": acc(key), "debit": dr, "credit": cr}


def jv(m, lines, d=D):
    doc = journals.save_journal(m, None, d, "test", "regular", lines)
    approval.approve(m, "manual_journal", doc)
    return doc


def row(df, label, col):
    return float(df.loc[df["البند"].str.strip() == label, col].iloc[0])


@pytest.fixture()
def books(master):
    """Capital 100,000 in cash; a cash sale of 5,000; admin salaries 1,000; a machine of 12,000 bought for cash."""
    jv(master, [line("cash_main", dr=100000), line("capital", cr=100000)])
    jv(master, [line("cash_main", dr=5000), line("sales", cr=5000)], d=date(2026, 4, 10))
    jv(master, [line("admin_salaries", dr=1000), line("cash_main", cr=1000)], d=date(2026, 4, 20))
    jv(master, [line("fa_machinery", dr=12000), line("cash_main", cr=12000)], d=date(2026, 4, 25))
    return master


# ---------------------------------------------------------------- statements

def test_income_statement_balance_sheet_and_cash_flow(books):
    cols = statements.fiscal_months(date(2026, 4, 1), date(2026, 5, 31))
    assert [c[0] for c in cols] == ["2026-04", "2026-05"]
    pl = statements.income_statement(cols + [("السنة", date(2026, 4, 1), date(2026, 5, 31))])
    assert row(pl, "صافي المبيعات", "2026-04") == 5000
    assert row(pl, "صافي الربح (الخسارة)", "2026-04") == 4000
    assert row(pl, "صافي الربح (الخسارة)", "2026-05") == 0
    assert statements.net_profit(date(2026, 4, 1), date(2026, 5, 31)) == 4000

    bs = statements.balance_sheet([date(2026, 3, 31), date(2026, 4, 30)])
    assert row(bs, "الفرق (لازم يكون صفر)", "2026-04-30") == 0
    assert row(bs, "أرباح (خسائر) لم تقفل بعد", "2026-04-30") == 4000
    assert row(bs, "إجمالي الأصول", "2026-04-30") == 104000
    assert row(bs, "إجمالي الأصول", "2026-03-31") == 0

    cf, summary = statements.cash_flow(date(2026, 4, 1), date(2026, 4, 30))
    assert summary == {"opening": 0, "change": 92000, "closing": 92000, "matches": True}
    assert row(cf, "صافي التدفق من الأنشطة التشغيلية", "المبلغ") == 4000
    assert row(cf, "صافي التدفق من الأنشطة الاستثمارية", "المبلغ") == -12000
    assert row(cf, "صافي التدفق من الأنشطة التمويلية", "المبلغ") == 100000


def test_cash_transfer_is_not_a_flow(books):
    jv(books, [line("bank", dr=30000), line("cash_main", cr=30000)], d=date(2026, 4, 28))
    cf, summary = statements.cash_flow(date(2026, 4, 1), date(2026, 4, 30))
    assert summary["matches"] and summary["closing"] == 92000
    assert row(cf, "صافي التغير في النقدية", "المبلغ") == 92000


# ---------------------------------------------------------------- fixed assets

def asset(m, **kw):
    data = {"name": "مكبس 1", "account_id": acc("fa_machinery"), "department": "factory",
            "acquisition_date": date(2026, 4, 25), "cost": 12000, "life_months": 12,
            "depreciation_start": date(2026, 4, 1)} | kw
    return fixed_assets.save_asset(m, None, data)


def test_monthly_depreciation_posts_and_runs_in_order(books):
    asset(books)
    asset(books, name="مكتب", account_id=acc("fa_furniture"), department="admin", cost=2400, life_months=24,
          acquisition_date=date(2026, 3, 1), depreciation_start=date(2026, 3, 1), opening_date=date(2026, 3, 31),
          opening_accumulated=100)
    jv(books, [line("fa_furniture", dr=2400), line("ad_furniture", cr=100), line("capital", cr=2300)],
       d=date(2026, 3, 31))
    april = fixed_assets.compute_month(books, 2026, 4)
    with pytest.raises(BusinessError):
        fixed_assets.compute_month(books, 2026, 6)
    approval.approve(books, "depreciation", april)
    assert bal("5207") == 1000 and bal("6111") == 100
    assert bal("ad_machinery") == -1000 and bal("ad_furniture") == -200

    may = fixed_assets.compute_month(books, 2026, 5)
    approval.approve(books, "depreciation", may)
    with pytest.raises(BusinessError):
        approval.unapprove(books, "depreciation", april, "x")
    rec = fixed_assets.reconciliation(date(2026, 5, 31))
    assert rec["فرق التكلفة"].abs().sum() == 0 and rec["فرق المجمع"].abs().sum() == 0
    with pytest.raises(BusinessError):  # cost is locked once depreciated
        fixed_assets.save_asset(books, fixed_assets.get_asset(1)["id"], fixed_assets.get_asset(1) | {"cost": 9000})


def test_depreciation_stops_at_salvage(books):
    a = asset(books, cost=1000, salvage_value=100, life_months=2)
    with session_scope() as s:
        assert [l["amount"] for l in fixed_assets.plan_month(s, 2026, 4)] == [450]
    for m in (4, 5):
        approval.approve(books, "depreciation", fixed_assets.compute_month(books, 2026, m))
    with pytest.raises(BusinessError):
        fixed_assets.compute_month(books, 2026, 6)
    assert fixed_assets.register_frame(date(2026, 6, 30)).set_index("id").loc[a, "صافي القيمة"] == 100


def test_year_close_zeroes_the_income_statement_accounts(books):
    close = fixed_assets.compute_close(books, 2026, 4)
    approval.approve(books, "year_close", close)
    assert bal("sales") == 0 and bal("admin_salaries") == 0
    assert bal("retained_earnings") == -4000
    # The income statement still shows the year's profit; the balance sheet now has it in 3301.
    assert statements.net_profit(date(2026, 4, 1), date(2027, 3, 31)) == 4000
    bs = statements.balance_sheet([date(2027, 3, 31)])
    assert "أرباح (خسائر) لم تقفل بعد" not in bs["البند"].str.strip().tolist()
    assert row(bs, "الفرق (لازم يكون صفر)", "2027-03-31") == 0
    year = [c for c in checks.run_all(date(2027, 5, 1)) if c.title.startswith("إقفال السنوات")][0]
    assert year.status == checks.OK


def test_integrity_checks_flag_problems(books):
    asset(books)
    results = {c.title: c for c in checks.run_all(date(2026, 4, 30))}
    assert results["توازن ميزان المراجعة"].status == checks.OK
    assert all(c.status != checks.ERROR for c in results.values()), [(c.title, c.result) for c in results.values()]
    jv(books, [line("capital", dr=200000), line("bank", cr=200000)], d=date(2026, 4, 30))
    assert {c.title: c for c in checks.run_all(date(2026, 4, 30))}["أرصدة الخزينة والبنوك"].status == checks.ERROR
    year = [c for c in checks.run_all(date(2027, 5, 1)) if c.title.startswith("إقفال السنوات")][0]
    assert year.status == checks.WARN


# ---------------------------------------------------------------- payroll

def employee(m, name="أحمد", **kw):
    data = {"name": name, "department": "production", "pay_basis": "monthly", "monthly_salary": 6500,
            "hours_per_day": 10, "overtime_factor": 1} | kw
    return payroll.save_employee(m, None, data)


def test_hourly_rate_follows_the_workbook(master):
    e = employee(master)
    d = employee(master, "يومية", pay_basis="daily", daily_wage=250, hours_per_day=8)
    rates = payroll.employees_frame().set_index("id")["قيمة الساعة"]
    assert rates[e] == round(6500 / 300, 4)
    assert rates[d] == round(250 / 8, 4)


def test_day_figures_and_attendance_lock(books):
    e = employee(books, overtime_factor=1.5)
    payroll.save_day(books, date(2026, 4, 1), [{"employee_id": e, "status": "present", "time_in": "08:00",
                                                "time_out": "20:00", "bonus": 50}])
    payroll.save_day(books, date(2026, 4, 2), [{"employee_id": e, "status": "present", "time_in": "22:00",
                                                "time_out": "06:00", "deduction_hours": 1, "penalty": 20}])
    payroll.save_day(books, date(2026, 4, 3), [{"employee_id": e, "status": "absent"}])
    summary = payroll.attendance_frame(2026, 4)
    assert summary["الساعات"].tolist() == [10, 8, 0]
    assert summary["إضافي"].tolist() == [2, 0, 0]

    doc = payroll.compute_payroll(books, 2026, 4)
    sheet = payroll.payroll_sheet(doc).iloc[0]
    rate = 6500 / 300
    assert sheet["الأساسي"] == round(18 * rate, 2)
    assert sheet["قيمة الإضافي"] == round(2 * rate * 1.5, 2)
    assert sheet["قيمة الخصم"] == round(rate, 2)
    assert sheet["المستحق"] == round(round(18 * rate, 2) + round(3 * rate, 2) - round(rate, 2) + 50 - 20, 2)
    with pytest.raises(BusinessError):
        payroll.save_day(books, date(2026, 4, 5), [{"employee_id": e, "status": "present"}])


def test_full_month_with_rest_days_pays_the_salary(books):
    e = employee(books)
    payroll.fill_rest_days(books, 2026, 4)
    d = date(2026, 4, 1)
    while d.month == 4:
        if d.weekday() != 4:
            payroll.save_day(books, d, [{"employee_id": e, "status": "present"}])
        d = date.fromordinal(d.toordinal() + 1)
    doc = payroll.compute_payroll(books, 2026, 4)
    assert payroll.payroll_sheet(doc).iloc[0]["الأساسي"] == 6500


def test_payroll_entry_and_advances(books):
    worker = employee(books)
    clerk = employee(books, "محمد", department="admin", monthly_salary=3000)
    adv = payroll.save_advance(books, None, date(2026, 4, 5), worker, 3000, acc("cash_main"), installment=500)
    approval.approve(books, "employee_advance", adv)
    small = payroll.save_advance(books, None, date(2026, 4, 6), clerk, 500, acc("cash_main"))
    approval.approve(books, "employee_advance", small)
    assert bal("employee_advances") == 3500

    for day in range(1, 4):
        payroll.save_day(books, date(2026, 4, day), [
            {"employee_id": worker, "status": "present", "overtime_hours": 2, "bonus": 10},
            {"employee_id": clerk, "status": "present"}])
    doc = payroll.compute_payroll(books, 2026, 4)
    sheet = payroll.payroll_sheet(doc).set_index("employee_id")
    assert sheet.loc[worker, "خصم السلف"] == 500
    assert sheet.loc[clerk, "خصم السلف"] == 300  # the whole balance, capped at what was earned (30 h x 10)
    with pytest.raises(BusinessError):
        payroll.update_line(books, doc, worker, 30, 0, 4000)
    payroll.update_line(books, doc, worker, 30, 0, 600, "خصم جزئي بناء على طلبه")

    sheet = payroll.payroll_sheet(doc).set_index("employee_id")
    w = sheet.loc[worker]
    approval.approve(books, "payroll", doc)
    assert bal("5202") == round(w["قيمة الإضافي"] + 30, 2)
    assert bal("5201") == round(w["المستحق"] - w["قيمة الإضافي"] - 30, 2)
    assert bal("6101") == 1000 + 300
    assert bal("accrued_wages") == -round(sheet["الصافي"].sum(), 2)
    assert bal("employee_advances") == 3500 - 600 - 300

    with session_scope() as s:
        assert payroll.advance_balance(s, worker) == 2400
        assert payroll.suggested_deduction(s, worker, date(2026, 5, 31)) == 500
        assert payroll.advance_balance(s, clerk) == 200
    advances = {c.title: c for c in checks.run_all(date(2026, 4, 30))}["سلف الموظفين مقابل 1602"]
    assert advances.status == checks.OK
    with pytest.raises(BusinessError):  # the advance was recovered in an approved payroll
        approval.unapprove(books, "employee_advance", small, "x")
    with pytest.raises(BusinessError):
        payroll.save_day(books, date(2026, 4, 10), [{"employee_id": worker, "status": "present"}])
