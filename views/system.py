"""Master admin only: backups and system information."""
from datetime import datetime

import pandas as pd
import streamlit as st

from core.services import system as svc
from ui.common import guard, page_header


def render() -> None:
    guard("system")
    page_header("إدارة النظام", "متاحة للـ Master Admin فقط")
    st.write(f"**قاعدة البيانات:** {svc.database_label()}")
    stamp = f"{datetime.now():%Y%m%d_%H%M}"
    if svc.is_sqlite():
        path = svc.database_path()
        if path and path.exists():
            st.metric("حجم قاعدة البيانات", f"{path.stat().st_size / 1024:,.0f} KB")
        st.warning("لو التطبيق شغال على Streamlit Cloud، ملف SQLite بيتمسح مع أي إعادة تشغيل. "
                   "استخدم PostgreSQL (Neon) كما في ملف README.")
        st.download_button("⬇️ تحميل نسخة احتياطية من قاعدة البيانات", data=svc.backup_bytes,
                           file_name=f"manuf_backup_{stamp}.db", mime="application/octet-stream", type="primary")
    else:
        st.download_button("⬇️ تحميل نسخة احتياطية (كل الجداول في ملف Excel)", data=svc.backup_bytes,
                           file_name=f"manuf_backup_{stamp}.xlsx", type="primary",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.caption("Neon بيعمل نسخ احتياطية تلقائية كمان، والملف ده نسخة إضافية تحتفظ بيها عندك.")
    counts = svc.table_counts()
    st.subheader("عدد السجلات")
    st.dataframe(pd.DataFrame({"الجدول": list(counts), "عدد السجلات": list(counts.values())}),
                 hide_index=True, width="stretch")
