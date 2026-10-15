"""Master admin only: backups and system information."""
from datetime import datetime

import pandas as pd
import streamlit as st

from core.services import system as svc
from ui.common import guard, page_header


def render() -> None:
    guard("system")
    page_header("إدارة النظام", "متاحة للـ Master Admin فقط")
    path = svc.database_path()
    if path and path.exists():
        st.metric("حجم قاعدة البيانات", f"{path.stat().st_size / 1024:,.0f} KB")
        st.download_button(
            "⬇️ تحميل نسخة احتياطية من قاعدة البيانات",
            data=svc.backup_bytes,
            file_name=f"manuf_backup_{datetime.now():%Y%m%d_%H%M}.db",
            mime="application/octet-stream",
            type="primary",
        )
        st.caption("خد نسخة احتياطية بشكل دوري واحفظها خارج السيرفر.")
    counts = svc.table_counts()
    st.subheader("عدد السجلات")
    st.dataframe(pd.DataFrame({"الجدول": list(counts), "عدد السجلات": list(counts.values())}),
                 hide_index=True, width="stretch")
