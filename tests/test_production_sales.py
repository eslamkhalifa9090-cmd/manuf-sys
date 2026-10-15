from datetime import date

import pytest
from sqlalchemy import select

from core.db import session_scope
from core.models import Account, ItemGroup
from core.models.molds import FACTORY, PAIR, RETIRED, Mold
from core.services import (approval, items, journals, ledger, mold_docs, molds, parties, production, purchases, sales,
                           stock)
from core.services.errors import BusinessError

FY = (date(2026, 1, 1), date(2027, 12, 31))
D = date(2026, 3, 10)


def acc(key_or_code: str) -> int:
    with session_scope() as s:
        return s.scalar(select(Account.id).where((Account.system_key == key_or_code) | (Account.code == key_or_code)))


def bal(code: str) -> float:
    with session_scope() as s:
        dr, cr = ledger._sums(s).get(acc(code), (0, 0))
    return round(dr - cr, 2)


def group(kind: str) -> int:
    with session_scope() as s:
        return s.scalar(select(ItemGroup.id).where(ItemGroup.kind == kind))


def on_hand(item_id):
    with session_scope() as s:
        return stock.position(s, item_id)


def ok(x, doc_type, doc_id):
    approval.approve(x["m"], doc_type, doc_id)


def balanced():
    return ledger.is_balanced(ledger.tb_totals(ledger.trial_balance(*FY)))


@pytest.fixture()
def x(master):
    sup = parties.save_party(master, None, "مورد", "supplier")
    cus = parties.save_party(master, None, "عميل", "customer")
    powder = items.save_item(master, None, "بودرة", group("raw"), "كيلو")
    box = items.save_item(master, None, "علبة", group("packaging"), "عدد")
    compound = items.save_item(master, None, "تضريبة 21", group("compound"), "كيلو")
    shoe = items.save_item(master, None, "نعل 40", group("finished"), "جوز", pieces_per_unit=2)
    doc = purchases.save_invoice(master, None, "invoice", date(2026, 3, 1), sup, "credit",
                                 [{"item_id": powder, "qty": 100, "unit_price": 50},
                                  {"item_id": box, "qty": 200, "unit_price": 1}])
    approval.approve(master, "purchase_invoice", doc)
    return {"m": master, "sup": sup, "cus": cus, "powder": powder, "box": box, "compound": compound, "shoe": shoe}


def new_mold(x, cost=10000):
    mid = molds.save_mold(x["m"], None, {"name": "اسطمبة نعل 40", "ownership": FACTORY, "counter_unit": PAIR,
                                         "cavities": 4, "useful_life": 1000, "supplier_id": x["sup"]})
    doc = purchases.save_invoice(x["m"], None, "invoice", date(2026, 3, 1), x["sup"], "credit",
                                 [{"account_id": acc("1106"), "qty": 1, "unit_price": cost, "mold_id": mid}])
    ok(x, "purchase_invoice", doc)
    return mid


def run_compound(x, qty_in=20, qty_out=19, rate=2):
    run = production.save_run(x["m"], None, D, x["compound"], qty_out, [{"item_id": x["powder"], "qty": qty_in}],
                              conversion_rate=rate)
    ok(x, "compound_run", run)
    return run


def make_shoes(x, mold_id=None, so=None, d=D, approve=True):
    """200 pieces run: 180 good, 20 waste (5% normal = 9), 10 kg compound and 90 boxes."""
    po = production.save_order(x["m"], None, d, x["shoe"], 180,
                               [{"item_id": x["compound"], "qty": 10}, {"item_id": x["box"], "qty": 90}],
                               waste_pieces=20, normal_waste_pct=5, rates={"wages": 0.5, "energy": 0.3},
                               sales_order_id=so, mold_id=mold_id, shots=50 if mold_id else 0)
    if approve:
        ok(x, "production_order", po)
    return po


def test_compound_run_moves_materials_through_wip(x):
    run_compound(x)
    assert on_hand(x["powder"]) == (80, 4000)
    assert on_hand(x["compound"]) == (19, 1038)  # 20 kg x 50 + 19 kg x 2 conversion
    assert bal("1303") == 0 and bal("5301") == -38
    assert production.runs_frame(x["m"]).iloc[0]["فرق الوزن"] == 1
    with pytest.raises(BusinessError):
        production.save_run(x["m"], None, D, x["shoe"], 1, [{"item_id": x["powder"], "qty": 1}])  # not a compound
    assert balanced()


def test_production_order_costs_mold_conversion_and_abnormal_waste(x):
    run_compound(x)
    mid = new_mold(x)
    po = make_shoes(x, mold_id=mid, approve=False)
    material = round(1038 / 19 * 10, 2) + 90
    conversion = 0.8 * 200
    mold = 100 * 10  # 50 shots x 4 cavities = 200 pieces = 100 pairs at 10,000 / 1,000 pairs
    total = material + conversion + mold
    abnormal = round((20 - 9) * total / 200, 2)
    c = production.preview_costing(po)
    assert (c["mold_units"], c["mold_rate"], c["mold_amount"]) == (100, 10, 1000)
    assert c["abnormal"] == pytest.approx(abnormal)
    ok(x, "production_order", po)
    assert on_hand(x["shoe"]) == (90, pytest.approx(round(total - abnormal, 2)))
    assert bal("1303") == 0 and bal("1206") == -1000 and bal("5402") == pytest.approx(abnormal)
    assert bal("5301") == -(38 + conversion)
    with session_scope() as s:
        p = molds.position(s, s.get(Mold, mid))
    assert p["used"] == 100 and p["accumulated"] == 1000
    # The production usage row belongs to the order: no hand edits.
    usage = molds.usage_frame(mid)
    assert len(usage) == 1
    with pytest.raises(BusinessError):
        molds.delete_usage(x["m"], int(usage.iloc[0]["id"]))
    assert balanced()


def test_monthly_amortization_takes_only_hand_typed_usage(x):
    run_compound(x)
    mid = new_mold(x)
    make_shoes(x, mold_id=mid)  # 100 pairs charged to the order (1,000)
    molds.save_usage(x["m"], None, mid, date(2026, 3, 20), shots=50)  # another 100 pairs typed in
    doc = mold_docs.compute_month(x["m"], 2026, 3)
    lines = mold_docs.amortization_lines(doc)
    assert lines["الوحدات"].tolist() == [100] and lines["القيمة"].tolist() == [1000]  # 9,000 / 900 pairs
    ok(x, "mold_amortization", doc)
    assert bal("1206") == -2000 and bal("5209") == 1000
    # A later order takes its rate after both: (10,000 - 2,000) / 800 = 10
    po = production.save_order(x["m"], None, date(2026, 4, 2), x["shoe"], 40, [{"item_id": x["box"], "qty": 20}],
                               mold_id=mid, shots=10)
    assert production.preview_costing(po)["mold_amount"] == 200


def test_unapproving_an_order_removes_its_usage_and_stock(x):
    run_compound(x)
    mid = new_mold(x)
    po = make_shoes(x, mold_id=mid)
    approval.unapprove(x["m"], "production_order", po, "تصحيح")
    assert molds.usage_frame(mid).empty
    assert on_hand(x["shoe"]) == (0, 0) and on_hand(x["compound"])[0] == 19
    assert bal("1206") == 0
    with session_scope() as s:
        assert molds.accumulated(s, s.get(Mold, mid)) == 0


def test_sales_order_invoice_return_and_progress(x):
    run_compound(x)
    so = sales.save_order(x["m"], None, D, x["cus"], [{"item_id": x["shoe"], "qty": 100, "unit_price": 30}],
                          customer_ref="PO-77")
    make_shoes(x, so=so)
    wac = on_hand(x["shoe"])[1] / 90
    pulled = sales.order_lines_for_invoice(so)
    assert pulled[0]["item_id"] == x["shoe"] and pulled[0]["qty"] == 100
    inv = sales.save_invoice(x["m"], None, "invoice", date(2026, 3, 15), x["cus"], "credit",
                             [pulled[0] | {"qty": 60}], discount=100, sales_order_id=so, dispatch_no="D-1")
    ok(x, "sales_invoice", inv)
    assert bal("4101") == -1800 and bal("4202") == 100 and bal("ar") == 1700
    assert bal("5101") == pytest.approx(round(60 * wac, 2), abs=0.02)
    assert on_hand(x["shoe"])[0] == 30
    over = sales.save_invoice(x["m"], None, "invoice", date(2026, 3, 16), x["cus"], "credit",
                              [{"item_id": x["shoe"], "qty": 40, "unit_price": 30}])
    with pytest.raises(BusinessError):  # only 30 pairs left in stock
        ok(x, "sales_invoice", over)
    sales.delete_invoice(x["m"], over)

    ret = sales.save_invoice(x["m"], None, "return", date(2026, 3, 18), x["cus"], "credit",
                             [{"item_id": x["shoe"], "qty": 10, "unit_price": 28.3333,
                               "order_line_id": pulled[0]["order_line_id"]}], sales_order_id=so, return_of_id=inv)
    with pytest.raises(BusinessError):
        sales.save_invoice(x["m"], None, "return", date(2026, 3, 18), x["cus"], "credit",
                           [{"item_id": x["shoe"], "qty": 61, "unit_price": 1}], return_of_id=inv)
    ok(x, "sales_invoice", ret)
    with session_scope() as s:
        cost_out = s.get(sales.SalesInvoice, inv).lines[0].unit_cost
    assert on_hand(x["shoe"])[0] == 40
    assert bal("5101") == pytest.approx(round(50 * cost_out, 2), abs=0.02)  # back at the cost it left with
    prog = sales.order_progress(so).iloc[0]
    assert (prog["المطلوب"], prog["اتنتج"], prog["اتسلّم"], prog["الباقي"]) == (100, 90, 50, 50)
    with pytest.raises(BusinessError):
        approval.unapprove(x["m"], "sales_invoice", inv, "x")  # an approved return points at it
    with pytest.raises(BusinessError):
        sales.save_order(x["m"], so, D, x["cus"], [{"id": pulled[0]["order_line_id"], "item_id": x["shoe"],
                                                    "qty": 40, "unit_price": 30}])  # below delivered
    profit = production.order_profit_frame().iloc[0]
    assert profit["الكمية المسلّمة"] == 50
    aging = sales.aging_frame(date(2026, 4, 30))
    assert aging.iloc[0]["الرصيد"] == pytest.approx(1700 - 283.33, abs=0.01)
    assert balanced()


def test_selling_a_mold_takes_it_off_the_books(x):
    run_compound(x)
    mid = new_mold(x)
    make_shoes(x, mold_id=mid)  # 1,000 amortized through the order
    inv = sales.save_invoice(x["m"], None, "invoice", date(2026, 3, 25), x["cus"], "credit",
                             [{"mold_id": mid, "qty": 1, "unit_price": 12000}])
    ok(x, "sales_invoice", inv)
    assert bal("4102") == -12000 and bal("1106") == 0 and bal("1206") == 0 and bal("5102") == 9000
    with session_scope() as s:
        assert s.get(Mold, mid).status == RETIRED
    with pytest.raises(BusinessError):
        sales.save_invoice(x["m"], None, "invoice", date(2026, 3, 26), x["cus"], "credit",
                           [{"mold_id": mid, "qty": 1, "unit_price": 1}])
    approval.unapprove(x["m"], "sales_invoice", inv, "x")
    with session_scope() as s:
        assert s.get(Mold, mid).status == "active"
    assert balanced()


def test_overhead_close_sends_the_difference_to_variances(x):
    run_compound(x)  # applies 38
    make_shoes(x)  # applies 160
    jv = journals.save_journal(x["m"], None, date(2026, 3, 28), "أجور مارس", "regular",
                               [{"account_id": acc("5201"), "debit": 150, "credit": 0},
                                {"account_id": acc("5202"), "debit": 90, "credit": 0},
                                {"account_id": acc("cash_main"), "debit": 0, "credit": 240}])
    ok(x, "manual_journal", jv)
    f = production.close_preview(2026, 3)
    assert (f["applied"], f["total_actual"], f["variance"]) == (198, 240, 42)
    doc = production.compute_close(x["m"], 2026, 3)
    ok(x, "overhead_close", doc)
    assert bal("5301") == 0 and bal("5201") == 0 and bal("5202") == 0 and bal("5404") == 42
    # The month is closed for production until the close is un-approved.
    with pytest.raises(BusinessError):
        make_shoes(x, d=date(2026, 3, 30))
    assert balanced()
