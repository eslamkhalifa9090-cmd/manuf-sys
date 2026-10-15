"""Runtime configuration read from environment variables or Streamlit secrets."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MANUF_DATA_DIR", BASE_DIR / "data"))
FONTS_DIR = BASE_DIR / "assets" / "fonts"
DB_URL_KEY = "MANUF_DB_URL"


def _secret_db_url() -> str | None:
    """Read the database URL from .streamlit/secrets.toml (or Streamlit Cloud secrets), if any."""
    try:
        import streamlit as st

        return st.secrets.get(DB_URL_KEY) or None
    except Exception:  # no secrets file, or not running under Streamlit
        return None


def normalize_db_url(url: str) -> str:
    """Accept the plain postgres:// URLs that Neon and other hosts hand out."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def db_url() -> str:
    url = os.environ.get(DB_URL_KEY) or _secret_db_url() or f"sqlite:///{DATA_DIR / 'manuf.db'}"
    return normalize_db_url(url.strip())
