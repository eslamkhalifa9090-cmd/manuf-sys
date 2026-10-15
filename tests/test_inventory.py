from datetime import date

import pandas as pd
import pytest
from sqlalchemy import select

from core.db import session_scope
from core.models import Account, ItemGroup
from core.services import approval, cash, items, journals, ledger, parties, purchases, stock, stock_vouchers
from core.services.errors import BusinessError

D = date(2026, 5, 10)
FY = (date(2026, 4, 1), date(2027, 3, 31))


def acc(key: str) -> int:
    with session_scope() as s:
        return s.scalar(select(Account.id).where((Account.system_key == key) | (Account.code == key)))


def group(kind: str) -> int:
    with session_scope() as s:
        return s.scalar(select(ItemGroup.id).where(ItemGroup.kind == kind))


@pytest.fixture()
def setup(master):
    sup = parties.save_party(master, None, "مورد الكاوتش", "supplier")
    cus = parties.save_party(master, None, "عميل الأمل", "customer")
    rubber = items.save_item(master, None, "كاوتش طبيعي", group("raw"), "كيلو")
    oil = items.save_item(master, None, "زيت صناعي", group("raw"), "لتر")
    sample = items.save_item(master, None, "عينة صبغة", group("samples"), "كيلو")
    return {"m": master, "sup": sup, "cus": cus, "rubber": rubber, "oil": oil, "sample": sample}


def buy(x, lines, discount=0, payment="credit", treasury=None, kind="invoice"):
    doc = purchases.save_invoice(x["m"], None, kind, D, x["sup"], payment, lines, discount, treasury)
    approval.approve(x["m"], "purchase_invoice", doc)
    return doc


def pos(item_id):
    with session_scope() as s:
        return stock.position(s, item_id)


def test_weighted_average_and_purchase_entry(setup):
    x = setup
    buy(x, [{"item_id": x["rubber"], "qty": 100, "unit_price": 10}])
    buy(x, [{"item_id": x["rubber"], "qty": 100, "unit_price": 14}])
    assert pos(x["rubber"]) == (200, 2400)
    bal = parties.balances_frame("supplier", FY[1])
    assert bal.loc[bal["الاسم"] == "مورد الكاوتش", "الرصيد"].iloc[0] == 2400
    rec = stock.reconciliation(FY[1])
    assert (rec["الفرق"] == 0).all()
    assert ledger.is_balanced(ledger.tb_totals(ledger.trial_balance(*FY)))


def test_invoice_discount_spreads_into_cost_and_direct_account_lines(setup):
    x = setup
    buy(x, [{"item_id": x["rubber"], "qty": 10, "unit_price": 100},
            {"account_id": acc("6108"), "qty": 1, "unit_price": 1000, "description": "صيانة"}], discount=200)
    assert pos(x["rubber"]) == (10, 900)
    stmt, info = ledger.account_statement(acc("6108"), *FY)
    assert info["closing"] == 900
    with pytest.raises(BusinessError):
        purchases.save_invoice(x["m"], None, "invoice", D, x["sup"], "credit",
                               [{"account_id": acc("inv_raw"), "qty": 1, "unit_price": 5}])


def test_issue_at_average_and_no_negative_stock(setup):
    x = setup
    buy(x, [{"item_id": x["rubber"], "qty": 10, "unit_price": 10}])
    buy(x, [{"item_id": x["rubber"], "qty": 20, "unit_price": 13}])  # avg 12
    v = stock_vouchers.save_voucher(x["m"], None, "issue", D, [{"item_id": x["rubber"], "qty": 5}],
                                    counter_account_id=acc("5208"))
    approval.approve(x["m"], "stock_voucher", v)
    assert pos(x["rubber"]) == (25, 300)
    v2 = stock_vouchers.save_voucher(x["m"], None, "count_loss", D, [{"item_id": x["rubber"], "qty": 26}])
    with pytest.raises(BusinessError, match="الرصيد غير كافي"):
        approval.approve(x["m"], "stock_voucher", v2)
    # Issuing everything leaves no value residue.
    v3 = stock_vouchers.save_voucher(x["m"], None, "count_loss", D, [{"item_id": x["rubber"], "qty": 25}])
    approval.approve(x["m"], "stock_voucher", v3)
    assert pos(x["rubber"]) == (0, 0)


def test_gifts_in_at_zero_do_not_touch_ledger_and_go_to_samples_only(setup):
    x = setup
    with pytest.raises(BusinessError, match="عينات"):
        stock_vouchers.save_voucher(x["m"], None, "gift_in", D, [{"item_id": x["rubber"], "qty": 5}])
    v = stock_vouchers.save_voucher(x["m"], None, "gift_in", D, [{"item_id": x["sample"], "qty": 5}], party_id=x["sup"])
    assert approval.approve(x["m"], "stock_voucher", v) is None
    assert pos(x["sample"]) == (5, 0)
    out = stock_vouchers.save_voucher(x["m"], None, "gift_out", D, [{"item_id": x["sample"], "qty": 2}], party_id=x["cus"])
    approval.approve(x["m"], "stock_voucher", out)
    assert pos(x["sample"]) == (3, 0)


def test_gift_out_charges_marketing(setup):
    x = setup
    buy(x, [{"item_id": x["oil"], "qty": 10, "unit_price": 50}])
    v = stock_vouchers.save_voucher(x["m"], None, "gift_out", D, [{"item_id": x["oil"], "qty": 2}], party_id=x["cus"])
    approval.approve(x["m"], "stock_voucher", v)
    assert ledger.account_statement(acc("gifts_expense"), *FY)[1]["closing"] == 100


def test_count_gain_opening_and_unapprove(setup):
    x = setup
    op = stock_vouchers.save_voucher(x["m"], None, "opening", D, [{"item_id": x["oil"], "qty": 10, "unit_cost": 20}])
    approval.approve(x["m"], "stock_voucher", op)
    assert ledger.account_statement(acc("opening_balance_equity"), *FY)[1]["closing"] == -200
    g = stock_vouchers.save_voucher(x["m"], None, "count_gain", D, [{"item_id": x["oil"], "qty": 2}])
    approval.approve(x["m"], "stock_voucher", g)
    assert pos(x["oil"]) == (12, 240)
    issue = stock_vouchers.save_voucher(x["m"], None, "issue", D, [{"item_id": x["oil"], "qty": 11}],
                                        counter_account_id=acc("5208"))
    approval.approve(x["m"], "stock_voucher", issue)
    with pytest.raises(BusinessError, match="بالسالب"):
        approval.unapprove(x["m"], "stock_voucher", op)
    approval.unapprove(x["m"], "stock_voucher", issue)
    assert pos(x["oil"]) == (12, 240)


def test_purchase_return_at_average_with_variance(setup):
    x = setup
    buy(x, [{"item_id": x["rubber"], "qty": 10, "unit_price": 10}])
    buy(x, [{"item_id": x["rubber"], "qty": 10, "unit_price": 14}])  # avg 12
    buy(x, [{"item_id": x["rubber"], "qty": 5, "unit_price": 14}], kind="return")
    assert pos(x["rubber"]) == (15, 180)
    assert ledger.account_statement(acc("cost_variance"), *FY)[1]["closing"] == -10
    bal = parties.balances_frame("supplier", FY[1])
    assert bal["الرصيد"].iloc[0] == 170
    assert ledger.is_balanced(ledger.tb_totals(ledger.trial_balance(*FY)))


def test_cash_purchase_and_supplier_payment_with_discount(setup):
    x = setup
    buy(x, [{"item_id": x["oil"], "qty": 1, "unit_price": 300}], payment="cash", treasury=acc("cash_main"))
    assert parties.balances_frame("supplier", FY[1])["الرصيد"].iloc[0] == 0
    buy(x, [{"item_id": x["oil"], "qty": 10, "unit_price": 100}])
    with pytest.raises(BusinessError, match="المورد"):
        cash.save_voucher(x["m"], None, "payment", D, acc("cash_main"), acc("ap"), 950)
    v = cash.save_voucher(x["m"], None, "payment", D, acc("cash_main"), acc("ap"), 950, party_id=x["sup"], discount=50)
    approval.approve(x["m"], "cash_voucher", v)
    assert parties.balances_frame("supplier", FY[1])["الرصيد"].iloc[0] == 0
    assert ledger.account_statement(acc("discount_received"), *FY)[1]["closing"] == -50
    stmt, info = parties.statement(x["sup"], *FY)
    assert info["closing"] == 0 and len(stmt) == 3


def test_customer_receipt_with_discount_and_opening_balances(setup):
    x = setup
    ob = journals.save_journal(x["m"], None, date(2026, 4, 1), "افتتاحي", "opening",
                               [{"account_id": acc("ar"), "debit": 5000, "party_id": x["cus"]}], auto_balance=True)
    approval.approve(x["m"], "manual_journal", ob)
    r = cash.save_voucher(x["m"], None, "receipt", D, acc("cash_main"), acc("ar"), 4900, party_id=x["cus"], discount=100)
    approval.approve(x["m"], "cash_voucher", r)
    assert parties.balances_frame("customer", FY[1])["الرصيد"].iloc[0] == 0
    rec = parties.reconciliation(FY[1])
    assert (rec["بدون عميل/مورد"] == 0).all()
    with pytest.raises(BusinessError, match="العميل"):
        journals.save_journal(x["m"], None, D, "x", "regular",
                              [{"account_id": acc("ar"), "debit": 10}, {"account_id": acc("cash_main"), "credit": 10}])


def test_imports(setup):
    x = setup
    df = pd.DataFrame({"اسم الصنف": ["ابر حقن - روز", "كاوتش طبيعي", "بودرة"], "المجموعة": ["قطع غيار ومهمات", "خامات", "؟"],
                       "الوحدة": ["عدد", "كيلو", "كيلو"]})
    checked = items.validate_import(df)
    assert list(checked["الحالة"]) == ["جاهز", "الصنف موجود", "المجموعة غير موجودة"]
    assert items.import_items(x["m"], df) == 1
    opening = pd.DataFrame({"اسم الصنف": ["ابر حقن - روز"], "الكمية": [303], "سعر الوحدة": [2.5]})
    assert stock_vouchers.import_voucher(x["m"], opening, "opening", date(2026, 4, 1)) == 1
    pdf = pd.DataFrame({"اسم العميل / المورد": ["مجدي السيسي", "عميل الأمل"], "النوع": ["عميل", "عميل"]})
    assert list(parties.validate_import(pdf)["الحالة"]) == ["جاهز", "الاسم موجود"]
    assert parties.import_parties(x["m"], pdf) == 1
    jdf = pd.DataFrame({"كود الحساب": ["1401", "1401"], "مدين": [100, 50], "دائن": [None, None],
                        "العميل / المورد": ["مجدي السيسي", ""]})
    assert list(journals.validate_lines_import(jdf)["الحالة"]) == ["جاهز", "الحساب ده محتاج اسم العميل / المورد"]


def test_items_with_moves_are_protected(setup):
    x = setup
    buy(x, [{"item_id": x["rubber"], "qty": 1, "unit_price": 1}])
    with pytest.raises(BusinessError):
        items.delete_item(x["m"], x["rubber"])
    with pytest.raises(BusinessError):
        items.save_item(x["m"], x["rubber"], "كاوتش طبيعي", group("spares"), "كيلو")
    with pytest.raises(BusinessError):
        parties.delete_party(x["m"], x["sup"])
