"""Entry point: `streamlit run app.py`."""
import streamlit as st

from core.bootstrap import init_db
from core.screens import SCREENS
from core.security import has_any_user, reload_user
from core.services.notifications import unread_count
from core.services.settings import get_settings
from ui.common import inject_css, show_flash
from views import (accounts, approvals, audit_log, auth, boms, cash_vouchers, company_settings, cost_card, cost_rates,
                   dashboard, item_card, items, journal_book, ledger, manual_journals, notifications, parties,
                   party_statement, periods, pricing, purchases, purchases_report, roles, stock_report, stock_vouchers,
                   system, trial_balance, users)

st.set_page_config(page_title="manuf-sys", page_icon="🏭", layout="wide")
inject_css()

VIEWS = {
    "dashboard": dashboard.render,
    "notifications": notifications.render,
    "approvals": approvals.render,
    "accounts": accounts.render,
    "manual_journals": manual_journals.render,
    "cash_vouchers": cash_vouchers.render,
    "journal_book": journal_book.render,
    "ledger": ledger.render,
    "trial_balance": trial_balance.render,
    "periods": periods.render,
    "parties": parties.render,
    "party_statement": party_statement.render,
    "items": items.render,
    "purchases": purchases.render,
    "stock_vouchers": stock_vouchers.render,
    "stock_report": stock_report.render,
    "item_card": item_card.render,
    "purchases_report": purchases_report.render,
    "boms": boms.render,
    "cost_rates": cost_rates.render,
    "pricing": pricing.render,
    "cost_card": cost_card.render,
    "company_settings": company_settings.render,
    "users": users.render,
    "roles": roles.render,
    "audit_log": audit_log.render,
    "system": system.render,
}


@st.cache_resource
def _init() -> bool:
    init_db()
    return True


def _sidebar(user) -> None:
    settings = get_settings()
    with st.sidebar:
        if settings.logo:
            st.image(settings.logo, width=110)
        st.markdown(f"### {settings.name_ar}")
        role = "Master Admin" if user.is_master else ""
        st.caption(f"👤 {user.full_name} {('· ' + role) if role else ''}")
        unread = unread_count(user.id)
        if unread:
            st.warning(f"🔔 عندك {unread} إشعار جديد — افتح شاشة الإشعارات")
        c1, c2 = st.columns(2)
        if c1.button("كلمة المرور", width="stretch"):
            auth.change_password_dialog(user)
        if c2.button("خروج", width="stretch"):
            st.session_state.clear()
            st.rerun()


def main() -> None:
    _init()
    if not has_any_user():
        auth.first_run_page()
        return
    if "user" not in st.session_state:
        auth.login_page()
        return
    # Re-read permissions every run so admin changes apply at once and disabled users are logged out.
    user = reload_user(st.session_state["user"].id)
    if user is None:
        st.session_state.clear()
        st.rerun()
    st.session_state["user"] = user
    if user.must_change_password:
        auth.change_password_form(user, forced=True)
        return

    _sidebar(user)
    show_flash()
    groups: dict[str, list] = {}
    for screen in SCREENS:
        if screen.key in VIEWS and user.can(screen.key, "view"):
            groups.setdefault(screen.module, []).append(
                st.Page(VIEWS[screen.key], title=screen.title, icon=screen.icon, url_path=screen.key,
                        default=screen.key == "dashboard")
            )
    if not groups:
        st.warning("لا توجد شاشات مسموح لك بها. تواصل مع مدير النظام.")
        return
    st.navigation(groups, expanded=True).run()


main()
