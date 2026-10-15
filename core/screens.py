"""Registry of application screens.

The key is what permissions are stored against. Each screen lists only the
actions that make sense for it; the permission matrix greys out the rest.
Add a screen here when its page ships.
"""
from dataclasses import dataclass

from core.models.security import ACTIONS


@dataclass(frozen=True)
class Screen:
    key: str
    title: str
    module: str
    actions: tuple[str, ...]
    icon: str = ":material/description:"
    # Master-only screens are never granted through roles.
    master_only: bool = False


ALL = ACTIONS
CRUD_IO = ("view", "add", "edit", "delete", "export", "import")

SCREENS: list[Screen] = [
    Screen("dashboard", "الرئيسية", "عام", ("view",), ":material/home:"),
    Screen("accounts", "شجرة الحسابات", "الحسابات العامة", CRUD_IO, ":material/account_tree:"),
    Screen("company_settings", "بيانات المنشأة والإعدادات", "الإعدادات", ("view", "edit"), ":material/settings:"),
    Screen("users", "المستخدمون", "الإعدادات", ("view", "add", "edit", "delete", "export"), ":material/group:"),
    Screen("roles", "الأدوار والصلاحيات", "الإعدادات", ("view", "add", "edit", "delete"), ":material/admin_panel_settings:"),
    Screen("audit_log", "سجل التدقيق", "الإعدادات", ("view", "export"), ":material/history:"),
    Screen("system", "إدارة النظام", "الإعدادات", ("view",), ":material/build:", master_only=True),
]

BY_KEY = {s.key: s for s in SCREENS}
