"""Company profile and report settings (a single row, id = 1)."""
from sqlalchemy import Boolean, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class CompanySettings(Base):
    __tablename__ = "company_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name_ar: Mapped[str] = mapped_column(String(150), default="NeW StAr")
    name_en: Mapped[str | None] = mapped_column(String(150))
    activity: Mapped[str | None] = mapped_column(String(150))
    address: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(80))
    email: Mapped[str | None] = mapped_column(String(120))
    commercial_register: Mapped[str | None] = mapped_column(String(60))
    tax_card: Mapped[str | None] = mapped_column(String(60))
    logo: Mapped[bytes | None] = mapped_column(LargeBinary)
    logo_mime: Mapped[str | None] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(String(10), default="ج.م")
    # Month the fiscal year (season) starts in; the workbook season starts in April.
    fiscal_year_start_month: Mapped[int] = mapped_column(Integer, default=4)
    decimals: Mapped[int] = mapped_column(Integer, default=2)
    # Report footer, printed on every PDF page and under every Excel export.
    footer_text: Mapped[str | None] = mapped_column(Text)
    # Comma-separated signature captions printed at the end of PDF reports.
    signature_labels: Mapped[str | None] = mapped_column(String(255), default="المحاسب,المراجع,المدير")
    show_page_numbers: Mapped[bool] = mapped_column(Boolean, default=True)
    show_print_date: Mapped[bool] = mapped_column(Boolean, default=True)
    show_printed_by: Mapped[bool] = mapped_column(Boolean, default=True)
    # Optional modules that only the master admin can switch on.
    tax_module_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    vat_rate: Mapped[float] = mapped_column(default=14.0)
