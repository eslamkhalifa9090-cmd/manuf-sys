"""Runtime configuration read from environment variables."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MANUF_DATA_DIR", BASE_DIR / "data"))
DB_URL = os.environ.get("MANUF_DB_URL", f"sqlite:///{DATA_DIR / 'manuf.db'}")
FONTS_DIR = BASE_DIR / "assets" / "fonts"
