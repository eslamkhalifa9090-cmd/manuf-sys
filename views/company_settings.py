"""Company profile, report footer and optional modules."""
import pandas as pd
import streamlit as st

from core.reports.exporters import to_pdf
from core.services import settings as svc
from ui.common import guard, page_header, run_action

SCREEN = "company_settings"
MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def render() -> None:
    user = guard(SCREEN)
    can_edit = user.can(SCREEN, "edit")
    page_header("بيانات المنشأة والإعدادات")
    st_ = svc.get_settings()

    tab_company, tab_reports, tab_logo, tab_policies = st.tabs(
        ["بيانات المنشأة", "شكل التقارير والفوتر", "لوجو المنشأة", "السياسات والموديولات"]
    )

    with tab_company:
        with st.form("company"):
            c1, c2 = st.columns(2)
            values = {
                "name_ar": c1.text_input("اسم المنشأة", st_.name_ar),
                "name_en": c2.text_input("الاسم بالإنجليزية", st_.name_en or ""),
                "activity": c1.text_input("النشاط", st_.activity or ""),
                "phone": c2.text_input("التليفون", st_.phone or ""),
                "address": c1.text_input("العنوان", st_.address or ""),
                "email": c2.text_input("البريد الإلكتروني", st_.email or ""),
                "commercial_register": c1.text_input("السجل التجاري", st_.commercial_register or ""),
                "tax_card": c2.text_input("البطاقة الضريبية", st_.tax_card or ""),
                "currency": c1.text_input("العملة", st_.currency),
                "fiscal_year_start_month": c2.selectbox(
                    "بداية السنة المالية (الموسم)", range(1, 13), index=st_.fiscal_year_start_month - 1,
                    format_func=lambda m: MONTHS[m - 1],
                ),
                "decimals": c1.number_input("عدد الخانات العشرية", 0, 4, st_.decimals),
            }
            if st.form_submit_button("حفظ", type="primary", disabled=not can_edit):
                if run_action(svc.update_settings, user, values, success="تم حفظ بيانات المنشأة"):
                    st.rerun()

    with tab_reports:
        with st.form("reports"):
            footer = st.text_area(
                "نص الفوتر (يظهر تحت كل شاشة، وأسفل كل صفحة PDF وتحت كل ملف Excel)", st_.footer_text or "",
                height=100, disabled=not user.is_master,
                help="ممكن أكتر من سطر. تعديله لمستخدم الماستر بس." if user.is_master else "تعديل الفوتر لمستخدم الماستر بس.",
            )
            values = {
                "signature_labels": st.text_input(
                    "خانات التوقيع في آخر التقرير (افصل بينها بفاصلة)", st_.signature_labels or "",
                    help="مثال: المحاسب,المراجع,المدير. سيبها فاضية لو مش عايز توقيعات.",
                ),
                "show_page_numbers": st.checkbox("إظهار رقم الصفحة", st_.show_page_numbers),
                "show_print_date": st.checkbox("إظهار تاريخ الطباعة", st_.show_print_date),
                "show_printed_by": st.checkbox("إظهار اسم من قام بالطباعة", st_.show_printed_by),
            }
            if user.is_master:
                values["footer_text"] = footer
            if st.form_submit_button("حفظ", type="primary", disabled=not can_edit):
                if run_action(svc.update_settings, user, values, success="تم حفظ إعدادات التقارير"):
                    st.rerun()
        sample = pd.DataFrame(
            {"البند": ["خامات", "تشغيل", "أسطمبة"], "القيمة": [4.60, 2.50, 0.35]}
        )
        st.download_button(
            "معاينة شكل التقرير PDF",
            data=lambda: to_pdf(sample, "معاينة تقرير", svc.get_settings(), subtitle="بيانات تجريبية",
                                printed_by=user.full_name, totals={"القيمة": 7.45}),
            file_name="preview.pdf", mime="application/pdf",
        )

    with tab_logo:
        if st_.logo:
            st.image(st_.logo, width=180)
        else:
            st.caption("لم يتم رفع لوجو بعد")
        if can_edit:
            up = st.file_uploader("رفع لوجو (PNG أو JPG، أقل من 2 ميجا)", type=["png", "jpg", "jpeg"])
            c1, c2 = st.columns(2)
            if up is not None and c1.button("حفظ اللوجو", type="primary"):
                if run_action(svc.set_logo, user, up.getvalue(), up.type, success="تم حفظ اللوجو"):
                    st.rerun()
            if st_.logo and c2.button("حذف اللوجو"):
                if run_action(svc.set_logo, user, None, None, success="تم حذف اللوجو"):
                    st.rerun()

    with tab_policies:
        st.markdown("**سياسات التكلفة** (ثابتة في النظام)")
        st.write("- تقييم المخزون: المتوسط المرجح (Weighted Average)")
        st.write("- التكاليف التحويلية: تحميل على الإنتاج (Absorption Costing)")
        st.divider()
        st.markdown("**الموديولات الاختيارية**")
        if user.is_master:
            with st.form("modules"):
                tax_on = st.toggle("تفعيل موديول الضرائب (القيمة المضافة والخصم والإضافة)", st_.tax_module_enabled)
                vat = st.number_input("نسبة ضريبة القيمة المضافة %", 0.0, 100.0, float(st_.vat_rate), step=0.5)
                if st.form_submit_button("حفظ", type="primary"):
                    if run_action(svc.update_settings, user, {"tax_module_enabled": tax_on, "vat_rate": vat},
                                  success="تم حفظ إعدادات الموديولات"):
                        st.rerun()
        else:
            state = "مفعّل" if st_.tax_module_enabled else "غير مفعّل"
            st.write(f"موديول الضرائب: {state}. التفعيل من صلاحيات الـ Master Admin فقط.")
