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
    # Public screens are open to every signed-in user and stay out of the permission matrix.
    public: bool = False


ALL = ACTIONS
CRUD_IO = ("view", "add", "edit", "delete", "export", "import")
# Documents that go through approval; own_only limits a user to the documents they created.
DOC_ACTIONS = ("view", "add", "edit", "delete", "export", "own_only")
REPORT = ("view", "export")

SCREENS: list[Screen] = [
    Screen("dashboard", "الرئيسية", "عام", ("view",), ":material/home:"),
    Screen("notifications", "الإشعارات والملاحظات", "عام", ("view",), ":material/notifications:", public=True),
    Screen("approvals", "الاعتمادات", "عام", ("view", "approve", "unapprove", "export"),
           ":material/task_alt:"),
    Screen("accounts", "شجرة الحسابات", "الحسابات العامة", CRUD_IO, ":material/account_tree:"),
    Screen("manual_journals", "قيود اليومية والأرصدة الافتتاحية", "الحسابات العامة", DOC_ACTIONS + ("import",),
           ":material/edit_note:"),
    Screen("cash_vouchers", "سندات القبض والصرف", "الحسابات العامة", DOC_ACTIONS, ":material/payments:"),
    Screen("journal_book", "دفتر اليومية", "الحسابات العامة", REPORT, ":material/menu_book:"),
    Screen("ledger", "كشف حساب", "الحسابات العامة", REPORT, ":material/receipt_long:"),
    Screen("trial_balance", "ميزان المراجعة", "الحسابات العامة", REPORT, ":material/balance:"),
    Screen("periods", "الفترات المحاسبية", "الحسابات العامة", ("view", "edit"), ":material/event_available:"),
    Screen("parties", "العملاء والموردين", "العملاء والموردين", CRUD_IO, ":material/contacts:"),
    Screen("party_statement", "كشف حساب عميل / مورد", "العملاء والموردين", REPORT, ":material/request_quote:"),
    Screen("items", "الأصناف ومجموعاتها", "المخازن والمشتريات", CRUD_IO, ":material/inventory_2:"),
    Screen("purchases", "فواتير ومرتجعات المشتريات", "المخازن والمشتريات", DOC_ACTIONS, ":material/shopping_cart:"),
    Screen("stock_vouchers", "أذون المخزن والتسويات", "المخازن والمشتريات", DOC_ACTIONS + ("import",),
           ":material/swap_vert:"),
    Screen("stock_report", "أرصدة المخزون", "المخازن والمشتريات", REPORT, ":material/warehouse:"),
    Screen("item_card", "كارت الصنف", "المخازن والمشتريات", REPORT, ":material/fact_check:"),
    Screen("purchases_report", "تقرير المشتريات", "المخازن والمشتريات", REPORT, ":material/receipt:"),
    Screen("company_settings", "بيانات المنشأة والإعدادات", "الإعدادات", ("view", "edit"), ":material/settings:"),
    Screen("users", "المستخدمون", "الإعدادات", ("view", "add", "edit", "delete", "export"), ":material/group:"),
    Screen("roles", "الأدوار والصلاحيات", "الإعدادات", ("view", "add", "edit", "delete"), ":material/admin_panel_settings:"),
    Screen("audit_log", "سجل التدقيق", "الإعدادات", ("view", "export"), ":material/history:"),
    Screen("system", "إدارة النظام", "الإعدادات", ("view",), ":material/build:", master_only=True),
]

BY_KEY = {s.key: s for s in SCREENS}
