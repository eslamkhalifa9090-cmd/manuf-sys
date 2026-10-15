# manuf-sys

نظام إدارة الحسابات والتكاليف والدورة الإنتاجية لمصنع منتجات الرابر (Rubber Manufacturing System).

**Python · Streamlit · SQLAlchemy · PostgreSQL / SQLite**

## التشغيل

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

افتح المتصفح على `http://localhost:8501`. أول مرة هيطلب منك إنشاء حساب الـ **Master Admin**.

- قاعدة البيانات بتتحفظ في `data/manuf.db` (غيّر المكان بمتغير البيئة `MANUF_DATA_DIR`).
- للتشغيل على شبكة المصنع: `streamlit run app.py --server.address 0.0.0.0` والمستخدمين يفتحوا `http://IP-الجهاز:8501`.
- النسخ الاحتياطي من شاشة **إدارة النظام** (للـ Master Admin فقط).

## التشغيل على Streamlit Community Cloud (من المتصفح)

Streamlit Cloud بيمسح الملفات المحلية مع كل إعادة تشغيل، فلازم قاعدة البيانات تكون **PostgreSQL خارجية** وإلا البيانات هتضيع.

1. **قاعدة البيانات (Neon، مجاني):**
   - ادخل [neon.tech](https://neon.tech) واعمل حساب، ثم **New Project** (اختار أقرب Region، مثلاً Frankfurt).
   - من **Connection Details** انسخ الـ Connection string، شكله:
     `postgresql://USER:PASSWORD@ep-xxxx.eu-central-1.aws.neon.tech/neondb?sslmode=require`
2. **التطبيق:**
   - ادخل [share.streamlit.io](https://share.streamlit.io) ← **Create app** ← اختار الريبو `manuf-sys`، الـ Branch `main`، الملف `app.py`.
   - قبل Deploy افتح **Advanced settings** ← **Secrets** والصق:
     ```toml
     MANUF_DB_URL = "postgresql://USER:PASSWORD@ep-xxxx.eu-central-1.aws.neon.tech/neondb?sslmode=require"
     ```
   - اضغط **Deploy**. أول فتح هيطلب إنشاء حساب الـ Master Admin، والجداول وشجرة الحسابات بتتعمل تلقائياً.
3. **الخصوصية:** من **Settings ← Sharing** خلّي التطبيق **Only specific people can view this app** وضيف إيميلات المستخدمين.

المتغير `MANUF_DB_URL` ممكن يتحط كـ Environment variable بدل الـ Secrets. لو مش موجود، النظام بيستخدم SQLite محلي (مناسب للتشغيل على جهاز واحد أو للتجربة).

## الاختبارات

```bash
python -m pytest
# على PostgreSQL:
MANUF_TEST_DB_URL="postgresql://user:pass@localhost/test_db" python -m pytest
```

## السياسات المحاسبية الثابتة

- أي حركة تتسجل بحالة *في انتظار الاعتماد* ولا تؤثر في الأرصدة أو التقارير إلا بعد اعتمادها.
- تقييم المخزون بالمتوسط المرجح (Weighted Average).
- التكاليف التحويلية تُحمّل على الإنتاج (Absorption Costing)، وفرق التحميل يُقفل على تكلفة المبيعات.

## هيكل المشروع

```
app.py                 نقطة الدخول والقائمة حسب الصلاحيات
core/
  models/              جداول قاعدة البيانات (SQLAlchemy)
  services/            منطق العمل (الشاشات لا تكتب في قاعدة البيانات مباشرة)
  seed/coa.py          شجرة الحسابات الافتراضية
  reports/exporters.py تصدير Excel و PDF بالعربي مع اللوجو والفوتر
  screens.py           سجل الشاشات (مفاتيح الصلاحيات)
  security.py          تسجيل الدخول والصلاحيات
ui/                    أدوات واجهة مشتركة (RTL، التصدير، الاستيراد، مصفوفة الصلاحيات)
views/                 شاشة لكل وحدة
tests/
```

## المراحل

| المرحلة | المحتوى | الحالة |
|---|---|---|
| 0 | الأساس: الدخول، المستخدمين والأدوار ومصفوفة الصلاحيات، الإعدادات واللوجو والفوتر، شجرة الحسابات، سجل التدقيق، التصدير والاستيراد | ✅ |
| 1 | محرك الاعتمادات والإشعارات، القيود اليدوية والأرصدة الافتتاحية، سندات القبض والصرف، ميزان المراجعة، كشف الحساب، دفتر اليومية، قفل الفترات | ✅ |
| 2 | الأصناف والمخازن والمشتريات والمتوسط المرجح والجرد والهدايا | |
| 3 | شاشة التسعير وكارتة تكلفة القطعة | |
| 4 | الأسطمبات والإهلاك | |
| 5 | الإنتاج المرحلي والمبيعات | |
| 6 | القوائم المالية والتحليلات والأجور | |
| 7 | ترحيل الأرصدة الافتتاحية من الإكسيل | |

الخطوط العربية في التقارير: Amiri (SIL Open Font License، انظر `assets/fonts/OFL.txt`).
