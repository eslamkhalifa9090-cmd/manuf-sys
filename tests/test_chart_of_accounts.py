import pandas as pd
import pytest
from sqlalchemy import select

from core.db import session_scope
from core.models import Account
from core.seed.coa import ACCOUNTS
from core.services import accounts as svc
from core.services.errors import BusinessError


def _by_code():
    with session_scope() as s:
        return {a.code: (a.parent.code if a.parent else None, a.is_postable, a.normal_side, a.account_type,
                         a.statement, a.pl_section, a.is_contra) for a in s.scalars(select(Account))}


def test_seed_builds_consistent_tree(db):
    accounts = _by_code()
    assert len(accounts) == len(ACCOUNTS)
    for code, (parent, postable, *_rest) in accounts.items():
        assert parent == svc.parent_code(code)
        has_children = any(p == code for p, *_ in accounts.values())
        assert postable is not has_children


def test_seed_is_idempotent(db):
    with session_scope() as s:
        assert svc.seed_chart(s) == 0


def test_types_sides_and_sections(db):
    a = _by_code()
    assert a["1301"][2:5] == ("debit", "asset", "BS")
    assert a["2101"][2:4] == ("credit", "liability")
    assert a["4101"][2:6] == ("credit", "revenue", "PL", "sales")
    # Contra accounts carry the opposite side of their class.
    assert a["1206"][2] == "credit" and a["1206"][6]
    assert a["4201"][2] == "debit" and a["4201"][5] == "sales_deductions"
    assert a["5301"][2] == "credit" and a["5301"][5] == "cogs"
    assert a["3201"][2] == "debit"
    assert a["7102"][5] == "selling"


def test_system_keys_needed_by_posting_rules_exist(db):
    needed = {"inv_raw", "inv_intermediate", "wip", "inv_finished", "inv_samples", "ar", "ap", "cash_main",
              "cash_vodafone", "cash_instapay", "sales", "cogs", "applied_conversion", "mold_asset",
              "mold_acc_amortization", "gifts_expense", "opening_balance_equity", "stock_gain", "stock_loss"}
    with session_scope() as s:
        keys = set(s.scalars(select(Account.system_key).where(Account.system_key.is_not(None))))
    assert needed <= keys


def test_create_child_turns_leaf_into_header_and_delete_restores(db, master):
    with session_scope() as s:
        parent_id = s.scalar(select(Account.id).where(Account.code == "6114"))
    assert svc.next_child_code(parent_id) == "611401"
    new_id = svc.create_account(master, parent_id, "نظافة")
    assert _by_code()["6114"][1] is False
    assert _by_code()["611401"][1] is True
    svc.delete_account(master, new_id)
    assert _by_code()["6114"][1] is True


def test_rejects_code_outside_parent_and_system_delete(db, master):
    with session_scope() as s:
        parent_id = s.scalar(select(Account.id).where(Account.code == "61"))
        system_id = s.scalar(select(Account.id).where(Account.code == "1401"))
    with pytest.raises(BusinessError):
        svc.create_account(master, parent_id, "x", code="7199")
    with pytest.raises(BusinessError):
        svc.delete_account(master, system_id)
    with pytest.raises(BusinessError):
        svc.update_account(master, system_id, "العملاء", None, is_active=False)


def test_import_validation_and_import(db, master):
    df = pd.DataFrame(
        {
            "كود الحساب": ["6115", "611501", "1401", "9", "7105"],
            "اسم الحساب": ["مصروفات نظافة", "منظفات", "مكرر", "خطأ", "مصروفات معارض"],
            "الاسم بالإنجليزية": ["", "", "", "", ""],
            "كود الحساب الأب": ["61", "6115", "14", "", "71"],
        }
    )
    checked = svc.validate_import(df)
    assert list(checked["الحالة"]) == ["جاهز", "جاهز", "الكود موجود", "الكود لازم يكون أرقام بطول 2 أو 4 أو 6", "جاهز"]
    assert svc.import_accounts(master, df) == 3
    accounts = _by_code()
    assert accounts["611501"][0] == "6115"
    assert accounts["6115"][1] is False
