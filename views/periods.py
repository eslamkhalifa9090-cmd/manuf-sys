"""Fiscal periods: close a month so nothing can be approved or un-approved in it."""
from datetime import date

import streamlit as st

from core.services import periods as svc
from core.services.settings import get_settings
from ui.common import guard, page_header, run_action

SCREEN = "periods"


def render() -> None:
    user = guard(SCREEN)
    page_header("الفترات المحاسبية", "الشهر المقفول مش بيقبل اعتماد أو إلغاء اعتماد أو تسجيل مستندات بتاريخه")
    start_month = get_settings().fiscal_year_start_month or 1
    this_year = date.today().year if date.today().month >= start_month else date.today().year - 1
    years = list(range(this_year - 3, this_year + 2))
    year = st.selectbox("السنة المالية التي تبدأ في", years, index=years.index(this_year), key="fp_year",
                        format_func=lambda y: f"{svc.MONTHS[start_month - 1]} {y}")
    df = svc.periods_frame(year, start_month)
    can_edit = user.can(SCREEN, "edit")
    edited = st.data_editor(
        df, hide_index=True, width="stretch", key=f"fp_{year}",
        disabled=[c for c in df.columns if c != "مقفولة" or not can_edit],
        column_config={"الشهر رقم": None},
    )
    if not can_edit:
        return
    changed = edited[edited["مقفولة"] != df["مقفولة"]]
    if st.button(f"حفظ التغييرات ({len(changed)})", type="primary", disabled=changed.empty):
        for rec in changed.to_dict("records"):
            if not run_action(svc.set_closed, user, int(rec["السنة"]), int(rec["الشهر رقم"]), bool(rec["مقفولة"])):
                return
        st.success("تم الحفظ")
        st.rerun()
