"""Master-admin maintenance: database backup and statistics."""
import io
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
from sqlalchemy import LargeBinary, inspect, select, text

from core.db import Base, get_engine


def is_sqlite() -> bool:
    return get_engine().url.get_backend_name() == "sqlite"


def database_label() -> str:
    url = get_engine().url
    if is_sqlite():
        return f"SQLite (ملف محلي: {url.database})"
    return f"PostgreSQL على {url.host}"


def database_path() -> Path | None:
    url = get_engine().url
    return Path(url.database) if is_sqlite() and url.database else None


def backup_bytes() -> bytes:
    """SQLite: a consistent copy of the database file. Other databases: every table in one Excel file."""
    path = database_path()
    if path is not None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "backup.db"
            src = sqlite3.connect(path)
            dst = sqlite3.connect(target)
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
            return target.read_bytes()
    return excel_dump()


def excel_dump() -> bytes:
    engine = get_engine()
    buf = io.BytesIO()
    with engine.connect() as conn, pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for table in Base.metadata.sorted_tables:
            cols = [c for c in table.columns if not isinstance(c.type, LargeBinary)]
            df = pd.read_sql(select(*cols), conn)
            df.to_excel(writer, sheet_name=table.name[:31], index=False)
    return buf.getvalue()


def table_counts() -> dict[str, int]:
    engine = get_engine()
    names = inspect(engine).get_table_names()
    with engine.connect() as conn:
        return {n: conn.execute(text(f'SELECT COUNT(*) FROM "{n}"')).scalar() for n in names if n in Base.metadata.tables}
