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
    Screen("boms", "التركيبات (BOM)", "التكاليف والتسعير", ("view", "add", "edit", "delete", "export"),
           ":material/science:"),
    Screen("cost_rates", "معدلات التحميل", "التكاليف والتسعير", ("view", "add", "delete", "export"),
           ":material/tune:"),
    Screen("pricing", "التسعير وعروض الأسعار", "التكاليف والتسعير", ("view", "add", "edit", "delete", "export"),
           ":material/sell:"),
    Screen("cost_card", "كارتة تكلفة القطعة", "التكاليف والتسعير", REPORT, ":material/request_page:"),
    Screen("molds", "سجل الأسطمبات", "الأسطمبات", ("view", "add", "edit", "delete", "export"),
           ":material/category:"),
    Screen("mold_usage", "عداد تشغيل الأسطمبات", "الأسطمبات", ("view", "add", "edit", "delete", "export"),
           ":material/speed:"),
    Screen("mold_maintenance", "صيانة وتعمير الأسطمبات", "الأسطمبات", DOC_ACTIONS, ":material/handyman:"),
    Screen("mold_amortization", "استهلاك الأسطمبات الشهري", "الأسطمبات", DOC_ACTIONS, ":material/trending_down:"),
    Screen("mold_settlement", "تسوية الأسطمبات (تحميل العميل / شطب)", "الأسطمبات", DOC_ACTIONS,
           ":material/gavel:"),
    Screen("mold_report", "تقرير الأسطمبات", "الأسطمبات", REPORT, ":material/monitoring:"),
    Screen("sales_orders", "أوامر العملاء", "المبيعات", ("view", "add", "edit", "delete", "export"),
           ":material/assignment:"),
    Screen("sales_invoices", "فواتير ومرتجعات المبيعات", "المبيعات", DOC_ACTIONS, ":material/point_of_sale:"),
    Screen("sales_report", "تقرير المبيعات", "المبيعات", REPORT, ":material/bar_chart:"),
    Screen("ar_aging", "أعمار ديون العملاء", "المبيعات", REPORT, ":material/hourglass_bottom:"),
    Screen("compound_runs", "تشغيلات التضريبة", "الإنتاج", DOC_ACTIONS, ":material/blender:"),
    Screen("production_orders", "أوامر الإنتاج", "الإنتاج", DOC_ACTIONS, ":material/precision_manufacturing:"),
    Screen("overhead_close", "إقفال الأعباء الشهري", "الإنتاج", DOC_ACTIONS, ":material/rule:"),
    Screen("production_report", "تقارير الإنتاج والربحية", "الإنتاج", REPORT, ":material/insights:"),
    Screen("income_statement", "قائمة الدخل", "القوائم المالية", REPORT, ":material/query_stats:"),
    Screen("balance_sheet", "قائمة المركز المالي", "القوائم المالية", REPORT, ":material/account_balance:"),
    Screen("cash_flow", "قائمة التدفقات النقدية", "القوائم المالية", REPORT, ":material/water:"),
    Screen("integrity_checks", "فحص سلامة البيانات", "القوائم المالية", ("view",), ":material/verified:"),
    Screen("year_close", "إقفال السنة المالية", "القوائم المالية", DOC_ACTIONS, ":material/lock_clock:"),
    Screen("fixed_assets", "سجل الأصول الثابتة", "الأصول الثابتة", ("view", "add", "edit", "delete", "export"),
           ":material/domain:"),
    Screen("depreciation", "الإهلاك الشهري", "الأصول الثابتة", DOC_ACTIONS, ":material/trending_down:"),
    Screen("employees", "الموظفين", "الأجور", ("view", "add", "edit", "delete", "export"), ":material/badge:"),
    Screen("attendance", "الحضور والانصراف", "الأجور", CRUD_IO, ":material/schedule:"),
    Screen("employee_advances", "سلف الموظفين", "الأجور", DOC_ACTIONS, ":material/money:"),
    Screen("payroll", "مسير المرتبات", "الأجور", DOC_ACTIONS, ":material/payments:"),
    Screen("payroll_report", "تقارير الأجور", "الأجور", REPORT, ":material/summarize:"),
    Screen("company_settings", "بيانات المنشأة والإعدادات", "الإعدادات", ("view", "edit"), ":material/settings:"),
    Screen("users", "المستخدمون", "الإعدادات", ("view", "add", "edit", "delete", "export"), ":material/group:"),
    Screen("roles", "الأدوار والصلاحيات", "الإعدادات", ("view", "add", "edit", "delete"), ":material/admin_panel_settings:"),
    Screen("audit_log", "سجل التدقيق", "الإعدادات", ("view", "export"), ":material/history:"),
    Screen("system", "إدارة النظام", "الإعدادات", ("view",), ":material/build:", master_only=True),
]

BY_KEY = {s.key: s for s in SCREENS}
