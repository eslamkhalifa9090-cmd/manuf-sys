"""A user's notifications: returned documents, un-approvals and reviewer notes."""
import pandas as pd
from sqlalchemy import func, select, update

from core.db import session_scope
from core.models import Notification


def unread_count(user_id: int) -> int:
    with session_scope() as s:
        return s.scalar(select(func.count(Notification.id))
                        .where(Notification.user_id == user_id, Notification.is_read.is_(False))) or 0


def list_for(user_id: int, unread_only: bool = False) -> pd.DataFrame:
    with session_scope() as s:
        q = select(Notification).where(Notification.user_id == user_id).order_by(Notification.created_at.desc())
        if unread_only:
            q = q.where(Notification.is_read.is_(False))
        rows = [{"id": n.id, "التاريخ": n.created_at, "المستند": n.doc_no or "", "العنوان": n.title,
                 "الملاحظة": n.message or "", "من": n.sender or "", "مقروء": n.is_read} for n in s.scalars(q)]
    return pd.DataFrame(rows, columns=["id", "التاريخ", "المستند", "العنوان", "الملاحظة", "من", "مقروء"])


def mark_read(user_id: int, ids: list[int] | None = None) -> None:
    """Mark the given notifications (or all) of this user as read."""
    with session_scope() as s:
        q = update(Notification).where(Notification.user_id == user_id).values(is_read=True)
        if ids is not None:
            q = q.where(Notification.id.in_(ids))
        s.execute(q)
