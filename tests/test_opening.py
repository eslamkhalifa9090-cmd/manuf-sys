from dataclasses import replace
from datetime import date

import pandas as pd
import pytest
from sqlalchemy import select

from core.db import session_scope
from core.models import Account, Item
from core.models.assets import FixedAsset
from core.models.molds import Mold
from core.services import approval, checks, journals, opening, parties, stock_vouchers
from core.services.errors import BusinessError

AS_OF = date(2026, 3, 31)


def bal(key: str) -> float:
    from core.models import JournalEntry, JournalLine
    from core.models.ledger import ENTRY_POSTED
    from sqlalchemy import func

    with session_scope() as s:
        acc = s.scalar(select(Account.id).where((Account.system_key == key) | (Account.code == key)))
        v = s.scalar(select(func.sum(JournalLine.debit - JournalLine.credit)).join(JournalEntry)
                     .where(JournalLine.account_id == acc, JournalEntry.status == ENTRY_POSTED))
    return round(float(v or 0), 2)


def frames(**over) -> dict[str, pd.DataFrame]:
    cols = {k: c for k, (_, c) in opening.SHEETS.items()}
    data = {
        "accounts": [["1501", "الخزينة", 200000, None, ""], ["3101", "رأس المال", None, 300000, ""]],
        "parties": [["عميل النور", "عميل", 27000, None, "0100", ""], ["مورد الكاوتش", "مورد", None, 43000, "", ""]],
        "stock": [["R-1", "كاوتش", "خامات", "كيلو", 100, 50]],
        "assets": [["", "مكبس 1", "1101", "المصنع", "2024-01-15", 120000, 0, 120, 26000]],
        "molds": [["", "أسطمبة جوان", "ملك المصنع", "", "قطعة", 4, 100000, 0, 9000, 3000, 20000],
                  ["", "أسطمبة العميل", "ملك العميل", "عميل النور", "جوز", 2, 0, 0, 0, 0, 0]],
    }
    data.update(over)
    return {k: pd.DataFrame(v, columns=cols[k]) for k, v in data.items()}


def test_template_round_trip(master):
    raw = opening.template(frames())
    back = opening.read_workbook(raw)
    assert list(back) == list(opening.SHEETS)
    assert back["parties"]["الاسم"].tolist() == ["عميل النور", "مورد الكاوتش"]
    empty = opening.read_workbook(opening.template())
    assert all(df.empty for df in empty.values())


def test_plan_totals(master):
    p = opening.plan(frames(), AS_OF)
    assert p.errors == 0
    # 200,000 + 27,000 + 120,000 + 9,000 debit; 300,000 + 43,000 + 26,000 + 3,000 credit; stock 5,000
    assert (p.debit, p.credit, p.stock_value) == (356000, 372000, 5000)
    assert p.equity_difference == -11000
    assert any("مش متزنة" in w for w in p.warnings)


def test_plan_rejects_bad_rows(master):
    f = frames(accounts=[["1401", "", 5, None, ""], ["1301", "", 5, None, ""], ["1101", "", 5, None, ""],
                         ["15", "", 5, None, ""], ["9999", "", 5, None, ""], ["1501", "", 5, 5, ""]],
               parties=[["س", "زبون", 1, None, "", ""], ["", "عميل", 1, None, "", ""]],
               stock=[["", "جديد", "مش موجودة", "كيلو", 1, 1], ["", "جديد 2", "خامات", "كيلو", 0, 1]],
               assets=[["", "أصل", "1106", "المصنع", "2024-01-01", 100, 0, 12, 0],
                       ["", "أصل 2", "1101", "المصنع", "2024-01-01", 100, 0, 12, 200]],
               molds=[["", "أسطمبة", "ملك العميل", "حد مش موجود", "قطعة", 1, 0, 0, 0, 0, 0]])
    p = opening.plan(f, AS_OF)
    assert p.errors == 13
    assert "العملاء والموردين" in p.checked["accounts"]["الحالة"].iloc[0]
    assert "المخزون" in p.checked["accounts"]["الحالة"].iloc[1]
    assert "الأصول" in p.checked["accounts"]["الحالة"].iloc[2]
    with pytest.raises(BusinessError, match="غير صالح"):
        opening.run(master, f, AS_OF)


def test_import_creates_everything_and_registers_match_gl(master):
    res = opening.run(master, frames(), AS_OF, "افتتاحي")
    assert res["created"] == {"parties": 2, "items": 1, "assets": 1, "molds": 2}
    with session_scope() as s:
        mold = s.scalar(select(Mold).where(Mold.name == "أسطمبة جوان"))
        assert (float(mold.opening_cost), float(mold.opening_amortization), float(mold.opening_usage)) == (9000, 3000, 20000)
        assert s.scalar(select(Mold).where(Mold.name == "أسطمبة العميل")).customer_id is not None
        asset = s.scalar(select(FixedAsset).where(FixedAsset.name == "مكبس 1"))
        assert asset.opening_date == AS_OF and float(asset.opening_accumulated) == 26000
        assert s.scalar(select(Item).where(Item.code == "R-1")).name == "كاوتش"
    approval.approve(master, "manual_journal", res["journal_id"])
    approval.approve(master, "stock_voucher", res["voucher_id"])
    assert bal("opening_balance_equity") == -res["equity_difference"] == 11000
    assert (bal("ar"), bal("ap"), bal("inv_raw")) == (27000, -43000, 5000)
    assert (bal("1101"), bal("1201"), bal("1106"), bal("1206")) == (120000, -26000, 9000, -3000)
    results = {c.title: c.status for c in checks.run_all(AS_OF)}
    for title, status in results.items():
        if "سجل" in title or "المخزون" in title or "ميزان" in title or "العملاء" in title:
            assert status == checks.OK, title


def test_balanced_opening_leaves_3303_at_zero(master):
    f = frames(accounts=[["1501", "", 200000, None, ""], ["3101", "", None, 289000, ""]])
    p = opening.plan(f, AS_OF)
    assert p.equity_difference == 0 and not p.warnings
    res = opening.run(master, f, AS_OF)
    approval.approve(master, "manual_journal", res["journal_id"])
    approval.approve(master, "stock_voucher", res["voucher_id"])
    assert bal("opening_balance_equity") == 0


def test_existing_masters_are_reused(master):
    parties.save_party(master, None, "عميل النور", "customer")
    p = opening.plan(frames(), AS_OF)
    assert p.checked["parties"]["ملاحظة"].tolist() == ["موجود", "جديد"]
    clash = frames(parties=[["عميل النور", "مورد", None, 5, "", ""]])
    assert opening.plan(clash, AS_OF).errors == 1
    res = opening.run(master, frames(), AS_OF)
    assert res["created"]["parties"] == 1
    # A second run would register the same assets and molds twice.
    assert opening.plan(frames(), AS_OF).errors == 3


def test_opening_permission_is_separate(master):
    journals_only = replace(master, is_master=False, perms={"manual_journals": {"view", "add", "import"},
                                          "stock_vouchers": {"view", "add"}})
    with session_scope() as s:
        cash = s.scalar(select(Account.id).where(Account.system_key == "cash_main"))
    line = [{"account_id": cash, "debit": 10, "credit": 0}]
    with pytest.raises(BusinessError):
        journals.save_journal(journals_only, None, AS_OF, "", "opening", line, auto_balance=True)
    with pytest.raises(BusinessError):
        opening.run(journals_only, frames(), AS_OF)
    with pytest.raises(BusinessError):
        stock_vouchers.save_voucher(journals_only, None, "opening", AS_OF, [])
    # The opening permission alone is enough, without rights on the registers it fills.
    opener = replace(master, is_master=False, perms={"opening_balances": {"view", "add", "import"}})
    res = opening.run(opener, frames(), AS_OF)
    assert res["journal_id"] and res["voucher_id"]
    assert journals.journals_frame(master).empty
    assert len(journals.journals_frame(master, "opening")) == 1
    assert stock_vouchers.vouchers_frame(master).empty
    with pytest.raises(BusinessError):
        journals.delete_journal(journals_only, res["journal_id"])


def test_values_longer_than_the_columns_are_rejected(master):
    f = frames(parties=[["عميل", "عميل", None, None, "0" * 61, ""]], stock=[["", "س" * 151, "خامات", "كيلو", 1, 1]])
    p = opening.plan(f, AS_OF)
    assert p.checked["parties"]["الحالة"].iloc[0] == "التليفون أطول من 60 حرف"
    assert p.checked["stock"]["الحالة"].iloc[0] == "اسم الصنف أطول من 150 حرف"
