"""Users, roles and the permission matrix."""
import pandas as pd
from sqlalchemy import func, select

from core.db import session_scope
from core.models import Role, RolePermission, User
from core.models.security import ACTIONS
from core.screens import SCREENS
from core.security import hash_password, validate_password, verify_password
from core.services import audit
from core.services.errors import BusinessError


# ---------- users ----------

def create_first_master(username: str, full_name: str, password: str) -> None:
    """First-run setup: create the master admin. Refused once any user exists."""
    err = validate_password(password)
    if err:
        raise BusinessError(err)
    if not (username or "").strip() or not (full_name or "").strip():
        raise BusinessError("اسم المستخدم والاسم بالكامل مطلوبين")
    with session_scope() as s:
        if s.scalar(select(func.count(User.id))):
            raise BusinessError("تم إعداد النظام بالفعل")
        user = User(username=username.strip(), full_name=full_name.strip(),
                    password_hash=hash_password(password), is_master=True)
        s.add(user)
        s.flush()
        audit.log(s, None, "create", "user", user.id, {"username": user.username, "is_master": True})


def list_users() -> pd.DataFrame:
    with session_scope() as s:
        rows = s.execute(
            select(User, Role.name).outerjoin(Role, Role.id == User.role_id).order_by(User.id)
        ).all()
        return pd.DataFrame(
            [
                {
                    "id": u.id,
                    "اسم المستخدم": u.username,
                    "الاسم": u.full_name,
                    "الدور": "Master Admin" if u.is_master else (role_name or "—"),
                    "نشط": u.is_active,
                    "آخر دخول": u.last_login.strftime("%Y-%m-%d %H:%M") if u.last_login else "",
                }
                for u, role_name in rows
            ],
            columns=["id", "اسم المستخدم", "الاسم", "الدور", "نشط", "آخر دخول"],
        )


def create_user(actor, username: str, full_name: str, password: str, role_id: int | None) -> int:
    err = validate_password(password)
    if err:
        raise BusinessError(err)
    username = (username or "").strip()
    if not username or not (full_name or "").strip():
        raise BusinessError("اسم المستخدم والاسم بالكامل مطلوبين")
    with session_scope() as s:
        if s.scalar(select(User.id).where(func.lower(User.username) == username.lower())):
            raise BusinessError("اسم المستخدم موجود بالفعل")
        user = User(username=username, full_name=full_name.strip(), password_hash=hash_password(password),
                    role_id=role_id, must_change_password=True)
        s.add(user)
        s.flush()
        audit.log(s, actor, "create", "user", user.id, {"username": username, "role_id": role_id})
        return user.id


def update_user(actor, user_id: int, full_name: str, role_id: int | None, is_active: bool) -> None:
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            raise BusinessError("المستخدم غير موجود")
        if user.is_master and not actor.is_master:
            raise BusinessError("لا يمكن تعديل حساب الـ Master Admin")
        if user.id == actor.id and not is_active:
            raise BusinessError("لا يمكنك إيقاف حسابك")
        user.full_name = full_name.strip() or user.full_name
        if not user.is_master:
            user.role_id = role_id
        user.is_active = is_active
        audit.log(s, actor, "update", "user", user.id, {"role_id": role_id, "is_active": is_active})


def reset_password(actor, user_id: int, new_password: str) -> None:
    err = validate_password(new_password)
    if err:
        raise BusinessError(err)
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            raise BusinessError("المستخدم غير موجود")
        if user.is_master and not actor.is_master:
            raise BusinessError("لا يمكن تغيير كلمة مرور الـ Master Admin")
        user.password_hash = hash_password(new_password)
        user.must_change_password = user.id != actor.id
        audit.log(s, actor, "reset_password", "user", user.id)


def change_own_password(actor, old_password: str, new_password: str) -> None:
    err = validate_password(new_password)
    if err:
        raise BusinessError(err)
    with session_scope() as s:
        user = s.get(User, actor.id)
        if user is None or not verify_password(old_password or "", user.password_hash):
            raise BusinessError("كلمة المرور الحالية غير صحيحة")
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        audit.log(s, actor, "change_password", "user", user.id)


def delete_user(actor, user_id: int) -> None:
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            return
        if user.is_master or user.id == actor.id:
            raise BusinessError("لا يمكن حذف هذا المستخدم")
        audit.log(s, actor, "delete", "user", user.id, {"username": user.username})
        s.delete(user)


# ---------- roles ----------

def list_roles() -> list[dict]:
    with session_scope() as s:
        counts = dict(s.execute(select(User.role_id, func.count(User.id)).group_by(User.role_id)).all())
        return [
            {"id": r.id, "name": r.name, "description": r.description, "users": counts.get(r.id, 0)}
            for r in s.scalars(select(Role).order_by(Role.name))
        ]


def save_role(actor, name: str, description: str | None, role_id: int | None = None) -> int:
    name = (name or "").strip()
    if not name:
        raise BusinessError("اسم الدور مطلوب")
    with session_scope() as s:
        dup = s.scalar(select(Role.id).where(func.lower(Role.name) == name.lower()))
        if dup and dup != role_id:
            raise BusinessError("يوجد دور بنفس الاسم")
        role = s.get(Role, role_id) if role_id else Role()
        role.name, role.description = name, description
        if not role_id:
            s.add(role)
        s.flush()
        audit.log(s, actor, "update" if role_id else "create", "role", role.id, {"name": name})
        return role.id


def delete_role(actor, role_id: int) -> None:
    with session_scope() as s:
        if s.scalar(select(func.count(User.id)).where(User.role_id == role_id)):
            raise BusinessError("الدور مستخدم، انقل المستخدمين لدور تاني الأول")
        role = s.get(Role, role_id)
        if role:
            audit.log(s, actor, "delete", "role", role_id, {"name": role.name})
            s.delete(role)


def permission_matrix(role_id: int) -> pd.DataFrame:
    """One row per grantable screen, one bool column per action."""
    with session_scope() as s:
        existing = {p.screen_key: p for p in s.scalars(select(RolePermission).where(RolePermission.role_id == role_id))}
    rows = []
    for sc in SCREENS:
        if sc.master_only or sc.public:
            continue
        p = existing.get(sc.key)
        row = {"screen_key": sc.key, "الوحدة": sc.module, "الشاشة": sc.title}
        for a in ACTIONS:
            row[a] = bool(p and getattr(p, f"can_{a}")) and a in sc.actions
        rows.append(row)
    return pd.DataFrame(rows)


def save_permission_matrix(actor, role_id: int, matrix: pd.DataFrame) -> None:
    from core.screens import BY_KEY

    with session_scope() as s:
        if s.get(Role, role_id) is None:
            raise BusinessError("الدور غير موجود")
        existing = {p.screen_key: p for p in s.scalars(select(RolePermission).where(RolePermission.role_id == role_id))}
        for rec in matrix.to_dict("records"):
            screen = BY_KEY.get(rec["screen_key"])
            if screen is None or screen.master_only or screen.public:
                continue
            p = existing.get(screen.key)
            if p is None:
                p = RolePermission(role_id=role_id, screen_key=screen.key)
                s.add(p)
            for a in ACTIONS:
                # Actions that do not apply to the screen are always stored as False.
                setattr(p, f"can_{a}", bool(rec.get(a)) and a in screen.actions)
        audit.log(s, actor, "update", "role_permissions", role_id)


VIEW_MODES = {"desktop": "كمبيوتر", "mobile": "موبايل"}


def set_view_mode(user_id: int, mode: str) -> None:
    """Remember the layout a user picked (no permission needed: it is their own preference)."""
    if mode not in VIEW_MODES:
        raise BusinessError("طريقة العرض غير معروفة")
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is not None:
            user.view_mode = mode
