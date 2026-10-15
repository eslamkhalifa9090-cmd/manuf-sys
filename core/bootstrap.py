"""Create tables and seed default data. Safe to run on every start."""
from core.db import Base, get_engine, session_scope
from core import models  # noqa: F401  (registers all tables)
from core.services import cash, journals  # noqa: F401  (register approval document types)
from core.services.accounts import seed_chart
from core.services.settings import ensure_settings


def init_db(url: str | None = None) -> None:
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        ensure_settings(s)
        seed_chart(s)
