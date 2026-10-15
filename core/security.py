"""Password hashing, login and permission checks."""
from dataclasses import dataclass, field
from datetime import datetime

import bcrypt
from sqlalchemy import func, select

from core.db import session_scope
from core.models import RolePermission, User
from core.models.security import ACTIONS
from core.screens import BY_KEY

MIN_PASSWORD_LENGTH = 6


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def validate_password(password: str) -> str | None:
    """Return an error message, or None when the password is acceptable."""
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"كلمة المرور لازم تكون {MIN_PASSWORD_LENGTH} حروف على الأقل"
    return None


@dataclass
class CurrentUser:
    id: int
    username: str
    full_name: str
    is_master: bool
    role_id: int | None
    must_change_password: bool = False
    # screen_key -> set of granted actions
    perms: dict[str, set[str]] = field(default_factory=dict)

    def can(self, screen_key: str, action: str = "view") -> bool:
        screen = BY_KEY.get(screen_key)
        if screen is None or action not in screen.actions:
            return False
        if action == "own_only":
            # A restriction, not a right: the master is never restricted.
            return not self.is_master and action in self.perms.get(screen_key, set())
        if self.is_master or (screen.public and action == "view"):
            return True
        if screen.master_only:
            return False
        return action in self.perms.get(screen_key, set())


def load_permissions(session, role_id: int | None) -> dict[str, set[str]]:
    if role_id is None:
        return {}
    rows = session.scalars(select(RolePermission).where(RolePermission.role_id == role_id))
    perms: dict[str, set[str]] = {}
    for row in rows:
        perms[row.screen_key] = {a for a in ACTIONS if getattr(row, f"can_{a}")}
    return perms


def to_current_user(session, user: User) -> CurrentUser:
    return CurrentUser(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        is_master=user.is_master,
        role_id=user.role_id,
        must_change_password=user.must_change_password,
        perms=load_permissions(session, user.role_id),
    )


def authenticate(username: str, password: str) -> CurrentUser | None:
    with session_scope() as s:
        user = s.scalar(select(User).where(func.lower(User.username) == (username or "").strip().lower()))
        if user is None or not user.is_active or not verify_password(password or "", user.password_hash):
            return None
        user.last_login = datetime.now()
        return to_current_user(s, user)


def reload_user(user_id: int) -> CurrentUser | None:
    """Re-read a user's flags and permissions (after an admin changes them)."""
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None or not user.is_active:
            return None
        return to_current_user(s, user)


def has_any_user() -> bool:
    with session_scope() as s:
        return (s.scalar(select(func.count(User.id))) or 0) > 0
