"""Users, roles, per-screen permissions and the audit trail."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base

# Order matters: it is the column order of the permission matrix.
ACTIONS = ("view", "add", "edit", "delete", "approve", "unapprove", "export", "import", "own_only")
ACTION_LABELS = {
    "view": "عرض",
    "add": "إضافة",
    "edit": "تعديل",
    "delete": "حذف",
    "approve": "اعتماد",
    "unapprove": "إلغاء اعتماد",
    "export": "تصدير",
    "import": "استيراد",
    "own_only": "سجلاته فقط",
}


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str | None] = mapped_column(String(255))
    permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    full_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(100))
    role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id", ondelete="SET NULL"))
    is_master: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    # Screen layout the user last chose at sign-in: desktop or mobile.
    view_mode: Mapped[str] = mapped_column(String(10), default="desktop")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    last_login: Mapped[datetime | None] = mapped_column(DateTime)

    role: Mapped[Role | None] = relationship()


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "screen_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"))
    screen_key: Mapped[str] = mapped_column(String(60))
    can_view: Mapped[bool] = mapped_column(Boolean, default=False)
    can_add: Mapped[bool] = mapped_column(Boolean, default=False)
    can_edit: Mapped[bool] = mapped_column(Boolean, default=False)
    can_delete: Mapped[bool] = mapped_column(Boolean, default=False)
    can_approve: Mapped[bool] = mapped_column(Boolean, default=False)
    can_unapprove: Mapped[bool] = mapped_column(Boolean, default=False)
    can_export: Mapped[bool] = mapped_column(Boolean, default=False)
    can_import: Mapped[bool] = mapped_column(Boolean, default=False)
    can_own_only: Mapped[bool] = mapped_column(Boolean, default=False)

    role: Mapped[Role] = relationship(back_populates="permissions")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    username: Mapped[str | None] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(40))
    entity: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(60))
    details: Mapped[str | None] = mapped_column(Text)
