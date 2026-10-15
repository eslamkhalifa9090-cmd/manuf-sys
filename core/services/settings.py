"""Company settings (single row)."""
from core.db import session_scope
from core.models import CompanySettings
from core.services import audit
from core.services.errors import BusinessError

EDITABLE_FIELDS = (
    "name_ar", "name_en", "activity", "address", "phone", "email",
    "commercial_register", "tax_card", "currency", "fiscal_year_start_month", "decimals",
    "footer_text", "signature_labels", "show_page_numbers", "show_print_date", "show_printed_by",
)
MASTER_ONLY_FIELDS = ("tax_module_enabled", "vat_rate")
MAX_LOGO_BYTES = 2 * 1024 * 1024


def ensure_settings(session) -> CompanySettings:
    row = session.get(CompanySettings, 1)
    if row is None:
        row = CompanySettings(id=1)
        session.add(row)
        session.flush()
    return row


def get_settings() -> CompanySettings:
    """Detached copy of the settings row, safe to read after the session closes."""
    with session_scope() as s:
        row = ensure_settings(s)
        s.expunge(row)
        return row


def update_settings(actor, values: dict) -> None:
    allowed = set(EDITABLE_FIELDS) | (set(MASTER_ONLY_FIELDS) if actor.is_master else set())
    illegal = set(values) - allowed
    if illegal:
        raise BusinessError("ليس لديك صلاحية تعديل: " + "، ".join(sorted(illegal)))
    if not (values.get("name_ar") or "").strip() and "name_ar" in values:
        raise BusinessError("اسم المنشأة مطلوب")
    with session_scope() as s:
        row = ensure_settings(s)
        changes = {}
        for key, value in values.items():
            if getattr(row, key) != value:
                changes[key] = value
                setattr(row, key, value)
        if changes:
            audit.log(s, actor, "update", "company_settings", 1, {k: v for k, v in changes.items()})


def set_logo(actor, data: bytes | None, mime: str | None) -> None:
    if data is not None and len(data) > MAX_LOGO_BYTES:
        raise BusinessError("حجم اللوجو أكبر من 2 ميجا")
    with session_scope() as s:
        row = ensure_settings(s)
        row.logo = data
        row.logo_mime = mime
        audit.log(s, actor, "update", "company_logo", 1, {"removed": data is None})
