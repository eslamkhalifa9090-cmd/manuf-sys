from datetime import date

import pytest
from sqlalchemy import select

from core.db import session_scope
from core.models import Account
from core.models.molds import AGREED, CUSTOMER_OWNED, FACTORY, OVERHAUL, PAIR, PIECE, RETIRED, ROUTINE, Mold
from core.services import approval, ledger, mold_docs, molds, parties, purchases, quotes
from core.services.errors import BusinessError

FY = (date(2026, 1, 1), date(2027, 12, 31))


def acc(code: str) -> int:
    with session_scope() as s:
        return s.scalar(select(Account.id).where(Account.code == code))


def bal(code: str) -> float:
    """Debit-minus-credit of an account over posted entries."""
    with session_scope() as s:
        dr, cr = ledger._sums(s).get(acc(code), (0, 0))
    return round(dr - cr, 2)


def ok(x, doc_type, doc_id):
    approval.approve(x["m"], doc_type, doc_id)


@pytest.fixture()
def x(master):
    sup = parties.save_party(master, None, "مورد أسطمبات", "supplier")
    cus = parties.save_party(master, None, "عميل", "customer")
    return {"m": master, "sup": sup, "cus": cus}


def new_mold(x, **kw):
    data = {"name": kw.pop("name", "نعل 40"), "ownership": FACTORY, "counter_unit": PAIR, "cavities": 4,
            "useful_life": 1000, "supplier_id": x["sup"]} | kw
    return molds.save_mold(x["m"], None, data)


def buy_mold(x, mold_id, amount, d=date(2026, 1, 5)):
    doc = purchases.save_invoice(x["m"], None, "invoice", d, x["sup"], "credit",
                                 [{"account_id": acc("1106"), "qty": 1, "unit_price": amount, "mold_id": mold_id}])
    ok(x, "purchase_invoice", doc)


def position(mold_id, as_of=None):
    with session_scope() as s:
        return molds.position(s, s.get(Mold, mold_id), as_of)


def test_usage_converts_shots_and_cavities_to_counter_units(x):
    pair = new_mold(x)
    piece = new_mold(x, name="جلدة", counter_unit=PIECE, cavities=6)
    u = molds.save_usage(x["m"], None, pair, date(2026, 1, 10), shots=100)  # 4 cavities -> 400 pieces = 200 pairs
    molds.save_usage(x["m"], None, piece, date(2026, 1, 10), shots=100, cavities=5)  # one cavity closed
    df = molds.usage_frame()
    assert set(df["بوحدة العداد"]) == {200.0, 500.0}
    with pytest.raises(BusinessError):
        molds.save_usage(x["m"], None, pair, date(2026, 1, 10), shots=10, cavities=5)  # more than the mold has
    molds.save_usage(x["m"], u, pair, date(2026, 1, 10), shots=100, cavities=3)
    assert position(pair)["used"] == 150


def test_purchase_line_on_1106_must_name_an_owned_mold(x):
    owned = new_mold(x)
    theirs = new_mold(x, name="ملك العميل", ownership=CUSTOMER_OWNED, customer_id=x["cus"], useful_life=0)
    line = {"account_id": acc("1106"), "qty": 1, "unit_price": 100}
    with pytest.raises(BusinessError):
        purchases.save_invoice(x["m"], None, "invoice", date(2026, 1, 5), x["sup"], "credit", [line])
    with pytest.raises(BusinessError):
        purchases.save_invoice(x["m"], None, "invoice", date(2026, 1, 5), x["sup"], "credit",
                               [line | {"mold_id": theirs}])
    doc = purchases.save_invoice(x["m"], None, "invoice", date(2026, 1, 5), x["sup"], "credit",
                                 [line | {"mold_id": owned}], discount=10)
    assert purchases.get_invoice(doc)["lines"][0]["mold_id"] == owned
    ok(x, "purchase_invoice", doc)
    assert position(owned)["cost"] == 90


def test_amortization_is_units_of_production_and_prospective_after_overhaul(x):
    mid = new_mold(x, salvage_value=1000)
    buy_mold(x, mid, 11000)  # depreciable 10,000 over 1,000 pairs -> 10 a pair
    molds.save_usage(x["m"], None, mid, date(2026, 1, 20), shots=100)  # 200 pairs
    jan = mold_docs.compute_month(x["m"], 2026, 1)
    assert mold_docs.amortization_lines(jan)["القيمة"].tolist() == [2000]
    ok(x, "mold_amortization", jan)
    assert bal("5209") == 2000 and bal("1206") == -2000

    # Overhaul in February: +2,000 cost, +400 pairs of life.
    mm = mold_docs.save_maintenance(x["m"], None, mid, date(2026, 2, 3), OVERHAUL, 2000, acc("1501"),
                                    extra_life=400)
    ok(x, "mold_maintenance", mm)
    rout = mold_docs.save_maintenance(x["m"], None, mid, date(2026, 2, 4), ROUTINE, 300, acc("1501"))
    ok(x, "mold_maintenance", rout)
    assert bal("5205") == 300 and bal("1106") == 13000
    molds.save_usage(x["m"], None, mid, date(2026, 2, 10), shots=60)  # 120 pairs
    # remaining base 13,000 - 1,000 - 2,000 = 10,000 over (1,400 - 200) = 1,200 pairs
    feb = mold_docs.compute_month(x["m"], 2026, 2)
    line = mold_docs.amortization_lines(feb).iloc[0]
    assert line["إهلاك الوحدة"] == pytest.approx(10000 / 1200, rel=1e-6)
    assert line["القيمة"] == pytest.approx(round(120 * 10000 / 1200, 2))
    ok(x, "mold_amortization", feb)
    assert ledger.is_balanced(ledger.tb_totals(ledger.trial_balance(*FY)))


def test_months_are_sequential_and_lock_usage(x):
    mid = new_mold(x)
    buy_mold(x, mid, 5000)
    molds.save_usage(x["m"], None, mid, date(2026, 1, 20), shots=10)
    molds.save_usage(x["m"], None, mid, date(2026, 2, 20), shots=10)
    with pytest.raises(BusinessError):
        mold_docs.compute_month(x["m"], 2026, 2)  # January first
    jan = mold_docs.compute_month(x["m"], 2026, 1)
    feb = mold_docs.compute_month(x["m"], 2026, 2)
    with pytest.raises(BusinessError):
        ok(x, "mold_amortization", feb)  # January not approved yet
    with pytest.raises(BusinessError):
        molds.save_usage(x["m"], None, mid, date(2026, 1, 25), shots=5)  # month already computed
    with pytest.raises(BusinessError):
        mold_docs.delete_amortization(x["m"], jan)  # February comes after it
    ok(x, "mold_amortization", jan)
    ok(x, "mold_amortization", feb)
    with pytest.raises(BusinessError):
        approval.unapprove(x["m"], "mold_amortization", jan)  # February still approved
    with pytest.raises(BusinessError):
        molds.save_mold(x["m"], mid, molds.get_mold(mid) | {"useful_life": 2000})  # life locked after amortization


def test_opening_balances_feed_the_rate(x):
    mid = new_mold(x, opening_date=date(2025, 12, 31), opening_cost=8000, opening_amortization=3000,
                   opening_usage=500)
    molds.save_usage(x["m"], None, mid, date(2026, 1, 10), shots=50)  # 100 pairs
    p = position(mid, date(2025, 12, 31))
    assert (p["book_value"], p["remaining"], p["rate"]) == (5000, 500, 10)
    jan = mold_docs.compute_month(x["m"], 2026, 1)
    assert mold_docs.amortization_lines(jan)["القيمة"].tolist() == [1000]
    with pytest.raises(BusinessError):
        molds.save_usage(x["m"], None, mid, date(2025, 12, 20), shots=5)  # before the opening date


def test_settlement_charges_customer_and_retires(x):
    mid = new_mold(x, ownership=AGREED, customer_id=x["cus"], agreed_qty=800, agreement_end=date(2026, 6, 30))
    buy_mold(x, mid, 10000)
    molds.save_usage(x["m"], None, mid, date(2026, 1, 20), shots=150)  # 300 pairs
    jan = mold_docs.compute_month(x["m"], 2026, 1)
    ok(x, "mold_amortization", jan)  # 3,000
    pv = mold_docs.settlement_preview(mid, date(2026, 7, 1))
    assert (pv["agreed"], pv["used"], pv["short"], pv["book_value"]) == (800, 300, 500, 7000)

    doc = mold_docs.save_settlement(x["m"], None, mid, date(2026, 7, 1), recovery_amount=5000, retire=True)
    prev = approval.preview("mold_settlement", doc)
    assert round(prev["مدين"].sum(), 2) == round(prev["دائن"].sum(), 2) == 15000
    ok(x, "mold_settlement", doc)
    assert bal("1401") == 5000 and bal("4301") == -5000
    assert bal("1106") == 0 and bal("1206") == 0 and bal("5401") == 7000
    with session_scope() as s:
        assert s.get(Mold, mid).status == RETIRED
    with pytest.raises(BusinessError):
        molds.save_usage(x["m"], None, mid, date(2026, 7, 5), shots=1)
    approval.unapprove(x["m"], "mold_settlement", doc)
    with session_scope() as s:
        assert s.get(Mold, mid).status != RETIRED


def test_retire_waits_for_pending_amortization(x):
    mid = new_mold(x)
    buy_mold(x, mid, 4000)
    molds.save_usage(x["m"], None, mid, date(2026, 1, 20), shots=10)
    mold_docs.compute_month(x["m"], 2026, 1)
    doc = mold_docs.save_settlement(x["m"], None, mid, date(2026, 2, 1), retire=True)
    with pytest.raises(BusinessError):
        ok(x, "mold_settlement", doc)


def test_customer_owned_mold_counts_only(x):
    mid = new_mold(x, ownership=CUSTOMER_OWNED, customer_id=x["cus"], useful_life=0)
    with pytest.raises(BusinessError):
        molds.save_mold(x["m"], None, {"name": "x", "ownership": CUSTOMER_OWNED, "counter_unit": PIECE,
                                       "cavities": 1, "customer_id": x["cus"], "opening_cost": 100,
                                       "opening_date": date(2025, 12, 31)})
    molds.save_usage(x["m"], None, mid, date(2026, 1, 20), shots=10)
    with pytest.raises(BusinessError):
        mold_docs.compute_month(x["m"], 2026, 1)  # nothing to amortize
    with pytest.raises(BusinessError):
        mold_docs.save_maintenance(x["m"], None, mid, date(2026, 1, 21), OVERHAUL, 100, acc("1501"), extra_life=10)


def test_quote_keeps_the_registered_mold(x, master):
    from core.services import items
    from core.models import ItemGroup

    with session_scope() as s:
        g = s.scalar(select(ItemGroup.id).where(ItemGroup.kind == "finished"))
        raw = s.scalar(select(ItemGroup.id).where(ItemGroup.kind == "raw"))
    shoe = items.save_item(master, None, "نعل", g, "جوز", pieces_per_unit=2)
    rubber = items.save_item(master, None, "مطاط", raw, "كيلو")
    mid = new_mold(x)
    buy_mold(x, mid, 12000)
    d = molds.quote_defaults(mid)
    assert d["cost"] == 12000 and d["life_pieces"] == 2000
    qid = quotes.save_quote(master, None, {"quote_date": date(2026, 1, 6), "item_id": shoe, "qty": 100,
                                           "pieces_per_unit": 2, "method": "manual", "mold_mode": "life",
                                           "mold_id": mid, "mold_name": d["name"], "mold_cost": d["cost"],
                                           "mold_life_pieces": d["life_pieces"], "ga_basis": "pct", "selling_basis": "pct",
                                           "lines": [{"component_id": rubber, "qty": 0.2, "manual_price": 50}]})
    assert quotes.get_quote(qid)["mold_id"] == mid
    with pytest.raises(BusinessError):
        molds.delete_mold(master, mid)


def test_report_warns_near_end_of_life(x):
    mid = new_mold(x, useful_life=100)
    buy_mold(x, mid, 1000)
    molds.save_usage(x["m"], None, mid, date(2026, 1, 20), shots=46)  # 92 pairs of 100
    df = molds.report_frame(date(2026, 1, 31))
    assert df.loc[df["id"] == mid, "تنبيه"].iloc[0]
    rec = molds.reconciliation(date(2026, 1, 31))
    assert rec is not None
