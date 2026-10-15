from datetime import date

import pytest
from sqlalchemy import select

from core.db import session_scope
from core.models import Account, ItemGroup
from core.models.costing import GA, LAST, MANUAL, MOLD_LIFE, MOLD_NONE, MOLD_ORDER, PCT, PER_PIECE, SELLING, WAC, WAGES
from core.services import approval, boms, cost_rates, items, journals, parties, purchases, quotes
from core.services.errors import BusinessError

D = date(2026, 5, 10)


def group(kind: str) -> int:
    with session_scope() as s:
        return s.scalar(select(ItemGroup.id).where(ItemGroup.kind == kind))


def acc(code: str) -> int:
    with session_scope() as s:
        return s.scalar(select(Account.id).where(Account.code == code))


@pytest.fixture()
def x(master):
    sup = parties.save_party(master, None, "مورد", "supplier")
    cus = parties.save_party(master, None, "عميل", "customer")
    powder = items.save_item(master, None, "بودرة", group("raw"), "كيلو")
    oil = items.save_item(master, None, "زيت", group("raw"), "كيلو")
    box = items.save_item(master, None, "علبة", group("packaging"), "عدد")
    compound = items.save_item(master, None, "تضريبة 21", group("compound"), "كيلو")
    shoe = items.save_item(master, None, "نعل 37", group("finished"), "جوز", pieces_per_unit=2)
    return {"m": master, "sup": sup, "cus": cus, "powder": powder, "oil": oil, "box": box, "compound": compound,
            "shoe": shoe}


def buy(x, lines):
    doc = purchases.save_invoice(x["m"], None, "invoice", D, x["sup"], "credit", lines)
    approval.approve(x["m"], "purchase_invoice", doc)


def price(item_id, method):
    with session_scope() as s:
        return boms.unit_price(s, item_id, method)


def test_bom_rolls_up_intermediate_without_stock(x):
    buy(x, [{"item_id": x["powder"], "qty": 100, "unit_price": 40}, {"item_id": x["oil"], "qty": 100, "unit_price": 20}])
    buy(x, [{"item_id": x["powder"], "qty": 100, "unit_price": 60}])  # WAC 50, last 60
    # 10 kg compound = 6 kg powder + 4 kg oil, 5% waste
    boms.save_bom(x["m"], x["compound"], [{"component_id": x["powder"], "qty": 6, "in_grams": False},
                                         {"component_id": x["oil"], "qty": 4, "in_grams": False}], 10, 5)
    assert price(x["compound"], WAC) == (pytest.approx((6 * 50 + 4 * 20) / 10 * 1.05), boms.SRC_BOM)
    assert price(x["compound"], LAST)[0] == pytest.approx((6 * 60 + 4 * 20) / 10 * 1.05)
    # one piece = 150 g compound + 1 box; a pair is 2 pieces
    boms.save_bom(x["m"], x["shoe"], [{"component_id": x["compound"], "qty": 150, "in_grams": True},
                                     {"component_id": x["box"], "qty": 0.5, "in_grams": False}])
    with session_scope() as s:
        assert boms.piece_weight(s, x["shoe"]) == 150
    lines = {l["component_id"]: l for l in boms.costed_lines(x["shoe"])}
    assert lines[x["compound"]]["qty"] == pytest.approx(0.15)
    assert lines[x["box"]][WAC] == 0 and lines[x["box"]]["wac_src"] == boms.SRC_NONE
    # pair price from the recipe = 2 x piece
    assert price(x["shoe"], WAC)[0] == pytest.approx(2 * 0.15 * 39.9)


def test_stock_on_hand_wins_over_recipe(x):
    buy(x, [{"item_id": x["powder"], "qty": 10, "unit_price": 50}])
    boms.save_bom(x["m"], x["compound"], [{"component_id": x["powder"], "qty": 1, "in_grams": False}])
    buy(x, [{"item_id": x["compound"], "qty": 10, "unit_price": 70}])
    assert price(x["compound"], WAC) == (70, boms.SRC_STOCK)


def test_bom_rejects_cycles_grams_on_counted_items_and_duplicates(x):
    boms.save_bom(x["m"], x["compound"], [{"component_id": x["powder"], "qty": 1000, "in_grams": True}])
    boms.save_bom(x["m"], x["shoe"], [{"component_id": x["compound"], "qty": 100, "in_grams": True}])
    with pytest.raises(BusinessError, match="تركيبة نفسه"):
        boms.save_bom(x["m"], x["compound"], [{"component_id": x["shoe"], "qty": 1, "in_grams": False}])
    with pytest.raises(BusinessError, match="تركيبة نفسه"):
        boms.save_bom(x["m"], x["shoe"], [{"component_id": x["shoe"], "qty": 1, "in_grams": False}])
    with pytest.raises(BusinessError, match="بالجرام"):
        boms.save_bom(x["m"], x["shoe"], [{"component_id": x["box"], "qty": 5, "in_grams": True}])
    with pytest.raises(BusinessError, match="متكررة"):
        boms.save_bom(x["m"], x["shoe"], [{"component_id": x["oil"], "qty": 5, "in_grams": True},
                                         {"component_id": x["oil"], "qty": 5, "in_grams": True}])
    with pytest.raises(BusinessError, match="تركيبة أو عرض"):
        items.delete_item(x["m"], x["powder"])


def test_rates_effective_dates_and_suggestions(x):
    cost_rates.save_rate(x["m"], WAGES, 1.5, date(2026, 1, 1))
    cost_rates.save_rate(x["m"], WAGES, 2.0, date(2026, 6, 1))
    cost_rates.save_rate(x["m"], GA, 10, date(2026, 1, 1), PCT)
    assert cost_rates.rates_on(date(2026, 5, 31))[WAGES]["value"] == 1.5
    assert cost_rates.rates_on(date(2026, 6, 1))[WAGES]["value"] == 2.0
    assert cost_rates.rates_on(date(2026, 6, 1))[GA]["basis"] == PCT
    with pytest.raises(BusinessError):
        cost_rates.save_rate(x["m"], WAGES, 5, D, PCT)
    j = journals.save_journal(x["m"], None, D, "مصروفات", "regular", [
        {"account_id": acc("5201"), "debit": 3000, "credit": 0},
        {"account_id": acc("5203"), "debit": 1000, "credit": 0},
        {"account_id": acc("6102"), "debit": 600, "credit": 0},
        {"account_id": acc("1501"), "debit": 0, "credit": 4600}])
    approval.approve(x["m"], "manual_journal", j)
    sug = cost_rates.suggestions(date(2026, 5, 1), date(2026, 5, 31), 1000).set_index("component")
    assert sug.loc[WAGES, "المقترح للقطعة"] == 3.0
    assert sug.loc["energy", "الفعلي في الفترة"] == 1000
    assert sug.loc[GA, "المقترح %"] == 15.0  # 600 / (3000 + 1000)


def quote_data(x, **kw):
    d = {"quote_date": D, "party_id": x["cus"], "item_id": x["shoe"], "qty": 500, "pieces_per_unit": 2,
         "piece_weight_g": 200, "method": WAC, "waste_pct": 10, "wages": 1, "energy": 0.5, "maintenance": 0.5,
         "other": 0, "mold_mode": MOLD_ORDER, "mold_name": "أسطمبة 37", "mold_cost": 10000, "mold_life_pieces": 0,
         "ga_basis": PCT, "ga_value": 10, "selling_basis": PER_PIECE, "selling_value": 0.5, "margin_pct": 20,
         "offered_price": None, "notes": "",
         "lines": [{"component_id": x["compound"], "qty": 0.2, "wac_price": 40, "last_price": 45, "manual_price": 50}]}
    d.update(kw)
    return d


def test_quote_calculation_three_methods(x):
    r = quotes.calc(quote_data(x))
    # WAC: materials 8, conversion 2, waste 1, mold 10000/1000 = 10, production 21, GA 2.1, selling .5
    w = r[WAC]
    assert (w["materials"], w["conversion"], w["waste"], w["mold"]) == (8, 2, pytest.approx(1), 10)
    assert w["total"] == pytest.approx(23.6)
    assert w["price_piece"] == pytest.approx(23.6 / 0.8)
    assert w["price_unit"] == pytest.approx(2 * 23.6 / 0.8)
    assert w["price_kg"] == pytest.approx(23.6 / 0.8 / 0.2)
    assert w["markup_pct"] == pytest.approx(25)
    assert r[LAST]["materials"] == 9 and r[MANUAL]["materials"] == 10
    assert quotes.calc(quote_data(x, mold_mode=MOLD_LIFE, mold_life_pieces=50000))[WAC]["mold"] == pytest.approx(0.2)
    assert quotes.calc(quote_data(x, mold_mode=MOLD_NONE))[WAC]["mold"] == 0
    offered = quotes.calc(quote_data(x, offered_price=59))[WAC]
    assert offered["offered_margin_pct"] == pytest.approx((29.5 - 23.6) / 29.5 * 100)


def test_quote_revisions_keep_old_snapshot_and_lock_when_accepted(x):
    q1 = quotes.save_quote(x["m"], None, quote_data(x))
    assert quotes.get_quote(q1)["doc_no"] == "Q-000001-v1"
    q2 = quotes.save_quote(x["m"], q1, quote_data(x, margin_pct=30))
    old, new = quotes.get_quote(q1), quotes.get_quote(q2)
    assert new["doc_no"] == "Q-000001-v2" and new["is_current"] and not old["is_current"]
    assert old["margin_pct"] == 20 and old["lines"][0]["wac_price"] == 40
    with pytest.raises(BusinessError, match="إصدار قديم"):
        quotes.save_quote(x["m"], q1, quote_data(x))
    assert len(quotes.quotes_frame()) == 1 and len(quotes.quotes_frame(current_only=False)) == 2
    quotes.delete_quote(x["m"], q2)
    assert quotes.get_quote(q1)["is_current"]
    quotes.set_status(x["m"], q1, "accepted")
    with pytest.raises(BusinessError, match="اتقبل"):
        quotes.save_quote(x["m"], q1, quote_data(x))
    with pytest.raises(BusinessError):
        quotes.delete_quote(x["m"], q1)
    assert quotes.save_quote(x["m"], None, quote_data(x)) and quotes.quotes_frame()["رقم العرض"].iloc[0] == "Q-000002-v1"
    card = quotes.cost_card(quotes.get_quote(q1))
    assert card.loc[card["البند"] == "التكلفة الكلية للقطعة", "المتوسط المرجح"].iloc[0] == pytest.approx(23.6)


def test_quote_validation_and_permissions(x, clerk):
    with pytest.raises(BusinessError, match="هامش"):
        quotes.save_quote(x["m"], None, quote_data(x, margin_pct=100))
    with pytest.raises(BusinessError, match="العمر الافتراضي"):
        quotes.save_quote(x["m"], None, quote_data(x, mold_mode=MOLD_LIFE))
    with pytest.raises(BusinessError, match="خامة"):
        quotes.save_quote(x["m"], None, quote_data(x, lines=[]))
    with pytest.raises(BusinessError, match="صلاحية"):
        quotes.save_quote(clerk, None, quote_data(x))
    with pytest.raises(BusinessError, match="صلاحية"):
        boms.save_bom(clerk, x["shoe"], [{"component_id": x["oil"], "qty": 1, "in_grams": True}])
    with pytest.raises(BusinessError, match="صلاحية"):
        cost_rates.save_rate(clerk, SELLING, 1, D)


def test_quote_keeps_entry_basis_and_grams_flag(x):
    lines = [{"component_id": x["compound"], "qty": 0.2, "in_grams": True, "wac_price": 40, "last_price": 45,
              "manual_price": 50}]
    q = quotes.save_quote(x["m"], None, quote_data(x, entry_basis="order", lines=lines))
    d = quotes.get_quote(q)
    assert d["entry_basis"] == "order" and d["lines"][0]["in_grams"] is True
    card = quotes.cost_card(d)
    assert card["الكمية للطلبية"].iloc[0] == "200.000 كيلو"  # 0.2 kg x 1000 pieces
    with pytest.raises(BusinessError, match="طريقة الإدخال"):
        quotes.save_quote(x["m"], None, quote_data(x, entry_basis="batch"))
