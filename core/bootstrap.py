"""Create tables and seed default data. Safe to run on every start."""
from sqlalchemy import inspect, text

from core.db import Base, get_engine, session_scope
from core import models  # noqa: F401  (registers all tables)
from core.services import cash, journals, mold_docs, purchases, stock_vouchers  # noqa: F401  (register approval document types)
from core.services.items import seed_groups
from core.services.accounts import seed_chart
from core.services.settings import ensure_settings


def add_missing_columns(engine) -> list[str]:
    """Light migration: add nullable columns that newer versions declare on existing tables."""
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    added = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}'
                default = getattr(col.default, "arg", None)
                if isinstance(default, (int, float)) and not isinstance(default, bool):
                    ddl += f" DEFAULT {default}"
                elif isinstance(default, bool):
                    ddl += f" DEFAULT {'TRUE' if default else 'FALSE'}"
                conn.execute(text(ddl))
                added.append(f"{table.name}.{col.name}")
    return added


def init_db(url: str | None = None) -> None:
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    with session_scope() as s:
        ensure_settings(s)
        seed_chart(s)
        seed_groups(s)
