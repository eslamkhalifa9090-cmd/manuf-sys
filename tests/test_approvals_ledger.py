from datetime import date

import pandas as pd
import pytest
from sqlalchemy import select

from core.db import session_scope
from core.models import Account, JournalEntry
from core.security import CurrentUser
from core.services import approval, cash, journals, ledger, notifications, periods
from core.services.accounts import create_account, delete_account
from core.services.errors import BusinessError

D = date(2026, 5, 10)


def acc(key_or_code: str) -> int:
    with session_scope() as s:
        q = select(Account.id).where((Account.system_key == key_or_code) | (Account.code == key_or_code))
        return s.scalar(q)


def jv(user, lines, kind="regular", auto=False, d=D):
    return journals.save_journal(user, None, d, "test", kind, lines, auto)


def line(key, dr=0, cr=0):
    return {"account_id": acc(key), "debit": dr, "credit": cr}


@pytest.fixture()
def writer(master):
    """A clerk who may write journals and vouchers, own documents only, but not approve."""
    perms = {"manual_journals": {"view", "add", "edit", "delete", "own_only"},
             "cash_vouchers": {"view", "add", "edit", "delete"}}
    from core.services import users

    uid = users.create_user(master, "writer", "Writer", "secret123", None)
    return CurrentUser(id=uid, username="writer", full_name="Writer", is_master=False, role_id=None, perms=perms)


def test_pending_document_does_not_touch_balances(master):
    jv(master, [line("cash_main", dr=500), line("capital", cr=500)])
    tb = ledger.trial_balance(date(2026, 1, 1), date(2026, 12, 31))
    assert tb.empty


def test_approve_posts_balanced_entry_and_unapprove_voids(master):
    doc_id = jv(master, [line("cash_main", dr=500), line("capital", cr=500)])
    entry_id = approval.approve(master, "manual_journal", doc_id)
    tb = ledger.trial_balance(date(2026, 1, 1), date(2026, 12, 31))
    totals = ledger.tb_totals(tb)
    assert totals["ختامي مدين"] == totals["ختامي دائن"] == 500
    assert ledger.is_balanced(totals)

    with pytest.raises(BusinessError):
        journals.save_journal(master, doc_id, D, "edit", "regular", [line("cash_main", dr=1), line("capital", cr=1)])

    approval.unapprove(master, "manual_journal", doc_id, "تصحيح")
    with session_scope() as s:
        assert s.get(JournalEntry, entry_id).status == "void"
    assert ledger.trial_balance(date(2026, 1, 1), date(2026, 12, 31)).empty
    # Editable again after un-approval.
    journals.save_journal(master, doc_id, D, "edit", "regular", [line("cash_main", dr=700), line("capital", cr=700)])
    approval.approve(master, "manual_journal", doc_id)
    assert ledger.tb_totals(ledger.trial_balance(date(2026, 1, 1), date(2026, 12, 31)))["ختامي مدين"] == 700


def test_unbalanced_and_header_accounts_rejected(master):
    with pytest.raises(BusinessError, match="غير متزن"):
        jv(master, [line("cash_main", dr=500), line("capital", cr=400)])
    with pytest.raises(BusinessError, match="رئيسي"):
        jv(master, [line("15", dr=500), line("capital", cr=500)])
    with pytest.raises(BusinessError):
        jv(master, [line("cash_main", dr=500, cr=500), line("capital", cr=500)])


def test_opening_balance_auto_balances_to_equity(master):
    doc_id = jv(master, [line("cash_main", dr=1000), line("accrued_wages", cr=300)], kind="opening", auto=True)
    j = journals.get_journal(doc_id)
    assert j["kind"] == "opening" and len(j["lines"]) == 3
    eq = [l for l in j["lines"] if l["account_id"] == acc("opening_balance_equity")][0]
    assert eq["credit"] == 700
    assert journals.journals_frame(master)["رقم القيد"].iloc[0].startswith("OB-")


def test_return_requires_note_and_notifies_creator(master, writer):
    doc_id = jv(writer, [line("cash_main", dr=50), line("capital", cr=50)])
    with pytest.raises(BusinessError):
        approval.return_for_correction(master, "manual_journal", doc_id, "  ")
    approval.return_for_correction(master, "manual_journal", doc_id, "الحساب غلط")
    assert notifications.unread_count(writer.id) == 1
    assert notifications.list_for(writer.id)["الملاحظة"].iloc[0] == "الحساب غلط"
    notifications.mark_read(writer.id)
    assert notifications.unread_count(writer.id) == 0
    # Writer fixes it; it goes back to pending.
    journals.save_journal(writer, doc_id, D, "fixed", "regular", [line("cash_main", dr=50), line("notes_receivable", cr=50)])
    pending = approval.documents_frame(("pending",))
    assert list(pending["ref"]) == [f"manual_journal:{doc_id}"]


def test_permissions(master, writer, clerk):
    doc_id = jv(master, [line("cash_main", dr=50), line("capital", cr=50)])
    with pytest.raises(BusinessError):
        approval.approve(writer, "manual_journal", doc_id)
    with pytest.raises(BusinessError, match="مستنداتك"):
        journals.delete_journal(writer, doc_id)
    assert journals.journals_frame(writer).empty
    with pytest.raises(BusinessError):
        jv(clerk, [line("cash_main", dr=50), line("capital", cr=50)])
    assert not master.can("manual_journals", "own_only")


def test_cash_vouchers(master):
    capital = acc("capital")
    r = cash.save_voucher(master, None, "receipt", D, acc("cash_main"), capital, 1000, "الشريك")
    p = cash.save_voucher(master, None, "payment", D, acc("cash_vodafone"), acc("accrued_wages"), 250, "مورد")
    with pytest.raises(BusinessError):
        cash.save_voucher(master, None, "payment", D, capital, acc("ap"), 10)
    done, errors = approval.run_many(approval.approve, master, [("cash_voucher", r), ("cash_voucher", p)])
    assert done == 2 and not errors
    stmt, info = ledger.account_statement(acc("15"), date(2026, 1, 1), date(2026, 12, 31))
    assert info["closing"] == 750 and info["is_header"]
    assert list(stmt["الرصيد"])[-1] == 750
    preview = approval.preview("cash_voucher", p)
    assert preview["مدين"].sum() == preview["دائن"].sum() == 250


def test_trial_balance_levels_and_opening(master):
    approval.approve(master, "manual_journal", jv(master, [line("cash_main", dr=100), line("capital", cr=100)],
                                                  d=date(2026, 4, 1)))
    approval.approve(master, "manual_journal", jv(master, [line("cash_instapay", dr=40), line("cash_main", cr=40)],
                                                  d=date(2026, 5, 1)))
    tb = ledger.trial_balance(date(2026, 5, 1), date(2026, 5, 31), level=1)
    assets = tb[tb["الكود"] == "1"].iloc[0]
    assert assets["افتتاحي مدين"] == 100 and assets["حركة مدين"] == 40 and assets["حركة دائن"] == 40
    assert assets["ختامي مدين"] == 100
    assert ledger.is_balanced(ledger.tb_totals(tb))
    book = ledger.journal_book(date(2026, 1, 1), date(2026, 12, 31))
    assert len(book) == 4


def test_closed_period_blocks_posting(master):
    doc_id = jv(master, [line("cash_main", dr=10), line("capital", cr=10)])
    periods.set_closed(master, 2026, 5, True)
    with pytest.raises(BusinessError, match="مقفولة"):
        approval.approve(master, "manual_journal", doc_id)
    with pytest.raises(BusinessError, match="مقفولة"):
        jv(master, [line("cash_main", dr=10), line("capital", cr=10)])
    periods.set_closed(master, 2026, 5, False)
    approval.approve(master, "manual_journal", doc_id)
    frame = periods.periods_frame(2026, 4)
    assert frame.loc[frame["الشهر رقم"] == 5, "عدد القيود"].iloc[0] == 1


def test_accounts_with_postings_are_protected(master):
    jv(master, [line("cash_main", dr=10), line("capital", cr=10)])
    with pytest.raises(BusinessError):
        create_account(master, acc("cash_main"), "فرعي")
    new_id = create_account(master, acc("15"), "خزينة فرعية")
    jv(master, [{"account_id": new_id, "debit": 5}, line("capital", cr=5)])
    with pytest.raises(BusinessError, match="عليه حركات"):
        delete_account(master, new_id)


def test_import_journal_lines(master):
    df = pd.DataFrame({"كود الحساب": ["1501", "3101", "9999"], "مدين": [100, None, 5], "دائن": [None, 100, None],
                       "البيان": ["", "", ""]})
    checked = journals.validate_lines_import(df)
    assert list(checked["الحالة"]) == ["جاهز", "جاهز", "الحساب غير موجود"]
    with pytest.raises(BusinessError):
        journals.import_journal(master, df, D, "x")
    assert journals.import_journal(master, df.iloc[:2], D, "x") == 2
