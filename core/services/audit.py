"""Audit trail: who changed what, and when."""
import json

import pandas as pd
from sqlalchemy import select

from core.db import session_scope
from core.models import AuditLog


def log(session, actor, action: str, entity: str, entity_id=None, details: dict | None = None) -> None:
    """Add an audit row inside the caller's transaction. `actor` is a CurrentUser or None."""
    session.add(
        AuditLog(
            user_id=getattr(actor, "id", None),
            username=getattr(actor, "username", None),
            action=action,
            entity=entity,
            entity_id=None if entity_id is None else str(entity_id),
            details=json.dumps(details, ensure_ascii=False, default=str) if details else None,
        )
    )


def list_log(limit: int = 2000, username: str | None = None, entity: str | None = None) -> pd.DataFrame:
    with session_scope() as s:
        q = select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit)
        if username:
            q = q.where(AuditLog.username == username)
        if entity:
            q = q.where(AuditLog.entity == entity)
        rows = s.scalars(q).all()
        return pd.DataFrame(
            [
                {
                    "التاريخ والوقت": r.ts,
                    "المستخدم": r.username,
                    "العملية": r.action,
                    "الكيان": r.entity,
                    "المعرف": r.entity_id,
                    "التفاصيل": r.details,
                }
                for r in rows
            ],
            columns=["التاريخ والوقت", "المستخدم", "العملية", "الكيان", "المعرف", "التفاصيل"],
        )
