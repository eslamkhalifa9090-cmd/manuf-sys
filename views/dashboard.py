import streamlit as st
from sqlalchemy import func, select

from core.db import session_scope
from core.models import Account, User
from core.services.settings import get_settings
from ui.common import guard, page_header


def render() -> None:
    user = guard("dashboard")
    settings = get_settings()
    page_header(f"أهلاً {user.full_name}", settings.name_ar)
    with session_scope() as s:
        n_accounts = s.scalar(select(func.count(Account.id)).where(Account.is_active)) or 0
        n_users = s.scalar(select(func.count(User.id)).where(User.is_active)) or 0
    c1, c2, c3 = st.columns(3)
    c1.metric("الحسابات النشطة", n_accounts)
    c2.metric("المستخدمون النشطون", n_users)
    c3.metric("طريقة تقييم المخزون", "المتوسط المرجح")
    st.divider()
    st.markdown(
        """
**سياسات النظام**
- أي حركة تتسجل بحالة *في انتظار الاعتماد* ولا تؤثر في الأرصدة أو التقارير إلا بعد اعتمادها.
- تقييم المخزون بطريقة **المتوسط المرجح (Weighted Average)**، وآخر سعر شراء متاح للمقارنة في التسعير.
- التكاليف التحويلية (أجور، طاقة، صيانة) تُحمّل على الإنتاج بطريقة **Absorption Costing**.
"""
    )
