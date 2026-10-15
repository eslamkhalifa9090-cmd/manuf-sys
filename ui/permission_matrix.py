"""Pure helpers behind the permission matrix editor (kept free of Streamlit for testing)."""
import pandas as pd

from core.models.security import ACTIONS
from core.screens import BY_KEY

ROW_ALL = "الكل"


def applicable(screen_key: str, action: str) -> bool:
    return action in BY_KEY[screen_key].actions


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Clear actions a screen does not support and recompute the row 'all' column."""
    df = df.copy()
    for a in ACTIONS:
        df[a] = [bool(v) and applicable(k, a) for k, v in zip(df["screen_key"], df[a])]
    df[ROW_ALL] = [
        all(df.at[i, a] for a in ACTIONS if applicable(k, a)) for i, k in zip(df.index, df["screen_key"])
    ]
    return df


def apply_edits(df: pd.DataFrame, edited_rows: dict) -> pd.DataFrame:
    """Apply st.data_editor 'edited_rows'. Ticking the row 'all' box sets every action in that row."""
    df = df.copy()
    for pos, changes in edited_rows.items():
        idx = df.index[int(pos)]
        if ROW_ALL in changes:
            for a in ACTIONS:
                df.at[idx, a] = bool(changes[ROW_ALL])
        for col, value in changes.items():
            if col in ACTIONS:
                df.at[idx, col] = bool(value)
    return normalize(df)


def set_column(df: pd.DataFrame, action: str, value: bool) -> pd.DataFrame:
    """'Select all' for one action column."""
    df = df.copy()
    df[action] = bool(value)
    return normalize(df)


def column_all(df: pd.DataFrame, action: str) -> bool:
    rows = [v for k, v in zip(df["screen_key"], df[action]) if applicable(k, action)]
    return bool(rows) and all(rows)
