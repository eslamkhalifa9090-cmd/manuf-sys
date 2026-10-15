"""Master-admin maintenance: database backup and statistics."""
import sqlite3
import tempfile
from pathlib import Path

from sqlalchemy import inspect, text

from core.db import Base, get_engine


def database_path() -> Path | None:
    url = get_engine().url
    return Path(url.database) if url.get_backend_name() == "sqlite" and url.database else None


def backup_bytes() -> bytes:
    """Consistent copy of the live SQLite database (safe while users are working)."""
    path = database_path()
    if path is None:
        raise RuntimeError("Backup is only available for SQLite")
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "backup.db"
        src = sqlite3.connect(path)
        dst = sqlite3.connect(target)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        return target.read_bytes()


def table_counts() -> dict[str, int]:
    engine = get_engine()
    names = inspect(engine).get_table_names()
    with engine.connect() as conn:
        return {n: conn.execute(text(f'SELECT COUNT(*) FROM "{n}"')).scalar() for n in names if n in Base.metadata.tables}
