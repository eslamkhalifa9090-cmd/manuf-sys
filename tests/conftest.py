import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.bootstrap import init_db  # noqa: E402
from core.db import Base, get_engine  # noqa: E402
from core.security import CurrentUser  # noqa: E402

# Set MANUF_TEST_DB_URL to a throwaway PostgreSQL database to run the suite against Postgres.
TEST_DB_URL = os.environ.get("MANUF_TEST_DB_URL")


@pytest.fixture()
def db(tmp_path):
    if TEST_DB_URL:
        Base.metadata.drop_all(get_engine(TEST_DB_URL))
        init_db(TEST_DB_URL)
    else:
        init_db(f"sqlite:///{tmp_path / 'test.db'}")
    yield


@pytest.fixture()
def master(db):
    from core.services import users

    users.create_first_master("admin", "Admin", "secret123")
    from core.security import authenticate

    return authenticate("admin", "secret123")


@pytest.fixture()
def clerk():
    """A non-master user with no permissions."""
    return CurrentUser(id=999, username="clerk", full_name="Clerk", is_master=False, role_id=None)
