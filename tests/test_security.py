import pandas as pd
import pytest

from core.security import authenticate, hash_password, reload_user, verify_password
from core.services import settings as settings_svc
from core.services import users as svc
from core.services.errors import BusinessError
from ui import permission_matrix as pm


def test_password_hashing():
    h = hash_password("abc12345")
    assert verify_password("abc12345", h)
    assert not verify_password("wrong", h)


def test_first_master_only_once(db):
    svc.create_first_master("admin", "Admin", "secret123")
    with pytest.raises(BusinessError):
        svc.create_first_master("admin2", "Admin 2", "secret123")
    assert authenticate("ADMIN", "secret123").is_master
    assert authenticate("admin", "bad") is None


def test_role_permissions_flow(db, master):
    role_id = svc.save_role(master, "أمين مخزن", None)
    uid = svc.create_user(master, "store", "أمين المخزن", "pass123", role_id)
    matrix = svc.permission_matrix(role_id)
    matrix.loc[matrix["screen_key"] == "accounts", ["view", "export", "approve"]] = True
    svc.save_permission_matrix(master, role_id, matrix)

    user = reload_user(uid)
    assert user.must_change_password
    assert user.can("accounts", "view") and user.can("accounts", "export")
    assert not user.can("accounts", "approve")  # not an action of this screen
    assert not user.can("accounts", "add")
    assert not user.can("system", "view")  # master only
    assert master.can("system", "view")


def test_inactive_user_cannot_log_in(db, master):
    uid = svc.create_user(master, "temp", "Temp", "pass123", None)
    svc.update_user(master, uid, "Temp", None, is_active=False)
    assert authenticate("temp", "pass123") is None
    assert reload_user(uid) is None


def test_non_master_cannot_touch_master_or_tax_module(db, master, clerk):
    with pytest.raises(BusinessError):
        svc.reset_password(clerk, master.id, "another1")
    with pytest.raises(BusinessError):
        settings_svc.update_settings(clerk, {"tax_module_enabled": True})
    settings_svc.update_settings(master, {"tax_module_enabled": True})
    assert settings_svc.get_settings().tax_module_enabled


def _matrix():
    return pm.normalize(pd.DataFrame(
        [
            {"screen_key": "accounts", **{a: False for a in pm.ACTIONS}},
            {"screen_key": "company_settings", **{a: False for a in pm.ACTIONS}},
        ]
    ))


def test_row_all_sets_only_applicable_actions():
    df = pm.apply_edits(_matrix(), {1: {pm.ROW_ALL: True}})
    row = df.iloc[1]
    assert row["view"] and row["edit"] and row[pm.ROW_ALL]
    assert not row["delete"] and not row["approve"]  # settings has no delete/approve


def test_column_select_all_and_row_all_recomputed():
    df = pm.set_column(_matrix(), "view", True)
    assert pm.column_all(df, "view")
    df = pm.apply_edits(df, {0: {"view": False}})
    assert not pm.column_all(df, "view")
    df = pm.set_column(df, "edit", True)
    df = pm.set_column(df, "view", True)
    assert bool(df.iloc[1][pm.ROW_ALL])  # settings: view + edit is everything it has
    assert not bool(df.iloc[0][pm.ROW_ALL])


def test_view_mode_is_remembered_per_user(master):
    from core.security import authenticate
    from core.services import users
    from core.services.errors import BusinessError

    assert master.view_mode == "desktop"
    users.set_view_mode(master.id, "mobile")
    assert authenticate("admin", "secret123").view_mode == "mobile"
    import pytest
    with pytest.raises(BusinessError):
        users.set_view_mode(master.id, "tablet")
