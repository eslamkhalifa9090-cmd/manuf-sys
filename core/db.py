"""Database engine, session factory and declarative base."""
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from core import config


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def get_engine(url: str | None = None):
    """Return the process-wide engine, creating it on first use."""
    global _engine, _SessionLocal
    if _engine is None or url is not None:
        url = config.normalize_db_url(url) if url else config.db_url()
        if url.startswith("sqlite"):
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            _engine = create_engine(url)
            event.listen(_engine, "connect", _sqlite_pragmas)
        else:
            # Hosted Postgres (e.g. Neon) closes idle connections; check and recycle them.
            _engine = create_engine(url, pool_pre_ping=True, pool_recycle=300, pool_size=5, max_overflow=5)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


@contextmanager
def session_scope():
    """Transactional scope: commit on success, roll back on any error."""
    get_engine()
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
