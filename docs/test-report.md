# گزارش آزمون

## اجرای F-04 — سیاست چندنقشی Union (۳ نوامبر ۲۰۲۶)

- `cd backend && DJANGO_SETTINGS_MODULE=config.settings_test python manage.py test leave.tests.test_multirole_policy_api leave.tests.test_frontend_api leave.tests.test_exports leave.tests.test_attachment_security` — **۶۵ تست موفق** روی SQLite. شامل API واقعی با Session/CSRF، ترکیب HR+Manager، scope شرکت، ثبت درخواست برای direct report خارج از HR assignment، Read/Write/Export/Download، انقضای RoleAssignment، محرمانگی، پیوست و export.
- `makemigrations --check --dry-run` — بدون migration جاافتاده؛ `manage.py check` — بدون مشکل. `git diff --check` موفق.
- SQLite شواهد PostgreSQL نیست؛ اجرای مجدد Migration و تست‌های F-04 روی PostgreSQL **NOT VERIFIED** است.
- اجرای گسترده‌تر شامل کل `test_workflow_api` و `test_calendar` با چند تست قدیمیِ دارای تاریخ ثابت `2026-11-02` پس از آن تاریخ شکست خورد؛ آزمون‌های موجود وابسته به تاریخ جاری‌اند. این تست‌ها برای F-04 تغییر نکردند. تست جدید ثبت درخواست با تاریخ آینده و سیاست تقویم تنظیم‌شده موفق است.
- اجرای کامل `DJANGO_SETTINGS_MODULE=config.settings_test python manage.py test --verbosity 1` — **۴۰۰ تست اجرا شد؛ ۱۶ skip، ۳ failure و ۶ error**. خطاهای workflow/calendar از تاریخ‌های ثابت گذشتهٔ `2026-11-02`، تست‌های production settings از نبود scanner command اجباری در محیط تست، و یک انتظار قدیمی Upload (`201` در برابر پاسخ جاری `202`) بودند؛ مجموعهٔ متمرکز F-04 در اجرای جداگانه ۶۵/۶۵ موفق شد.

## سخت‌سازی اسکن و دانلود پیوست‌های HR (۳ نوامبر ۲۰۲۶)

- تست‌های `leave.tests.test_attachment_security`، `CacheReadinessTests` و تست API مجاز دانلود — **۲۵ تست موفق** در SQLite؛ شامل clean-only download، scope مالک/HR، file missing/deleted/symlink، parser/MIME/size/path traversal، جلوگیری از نوشتن از مسیر symlink، scanner unavailable/reject/accept/timeout/crash/nonzero/invalid JSON/خروجی بزرگ، retry idempotent، Audit و purge.
- تست readiness پیکربندی scanner، `manage.py check`، `makemigrations --check --dry-run` و `py_compile` موفق شدند.
- provider واقعی malware scanner، protocol wrapper سازمانی، filesystem/volume و permissionهای deployment، backup/restore هماهنگ DB و فایل، مهاجرت دادهٔ واقعی و آزمون production/PostgreSQL در این محیط تأیید نشده‌اند. Production اکنون بدون `ZSH_ATTACHMENT_SCANNER_COMMAND` fail-fast می‌شود و هیچ فایل غیر clean قابل دانلود نیست؛ provider واقعی همچنان **NOT VERIFIED** است. راهنمای عملیات و محدودیت‌ها در `docs/attachment-security-fa.md` آمده‌اند.

## بازبینی HTTPS و reverse proxy (۲ نوامبر ۲۰۲۶)

- `DJANGO_SETTINGS_MODULE=config.settings_test python manage.py test tests.test_production_proxy_security --verbosity 1` — **۱۰ تست موفق**: HTTPS از proxy trusted، جعل هدر از peer untrusted، مقدار چندتایی forwarded proto، redirect HTTP، جلوگیری از loop، Host ناشناخته، HSTS، cookieهای Secure، CSRF origin مجاز/غیرمجاز و جداسازی تنظیمات test/dev از production.
- `DJANGO_SETTINGS_MODULE=config.settings_test python manage.py test --verbosity 1` — **۳۴۵ تست، موفق؛ ۱۴ مورد skip**. هشدار موجود SQLite برای قید `nulls_distinct` باقی است و اثبات PostgreSQL محسوب نمی‌شود.
- `DJANGO_SETTINGS_MODULE=config.settings_prod ... python manage.py check` با مقادیر environment آزمایشی — بدون مشکل؛ `check --deploy --tag security` نیز بدون هشدار امنیتی. مقادیر آزمایشی secret/DB برای بررسی settings بودند و credential محیط واقعی نیستند.
- توپولوژی proxy واقعی، گواهی TLS، firewall/direct-upstream isolation و health probe از شبکهٔ واقعی در repository تعریف یا قابل اجرا نیستند؛ این موارد staging/production را لازم دارند. راهنمای امن و تصمیم‌های operator در `docs/deployment.md` ثبت شده‌اند.

## اعتبارسنجی PostgreSQL (۲ نوامبر ۲۰۲۶)

- PostgreSQL 17.11 در سرویس Docker موقت؛ نسخهٔ Django 5.2.18 و psycopg 3.3.6.
- `DJANGO_SETTINGS_MODULE=config.settings_postgres_test ZSH_DB_HOST=127.0.0.1 ZSH_DB_PORT=55432 ZSH_DB_NAME=zsh ZSH_TEST_DB_NAME=test_zsh_final ZSH_DB_USER=postgres ZSH_DB_PASSWORD=postgres python manage.py test --verbosity 1` از پوشهٔ `backend` — **۳۳۵ تست، موفق**؛ بررسی سیستمی Django بدون مشکل. این اجرای تازه، پایگاه دادهٔ تست را ساخت و همهٔ migrationها را اجرا کرد.
- `python manage.py migrate --noinput` روی پایگاه دادهٔ خالی PostgreSQL 17.11 — مهاجرت‌های `leave.0001` تا `leave.0018` و migrationهای Django موفق.
- `leave.tests.test_postgresql` روی PostgreSQL — **۷ تست، موفق**: cast زیرپرس‌وجوی عددی به متن، رد شدن مقایسهٔ بدون cast، شناسهٔ غیرعددی، قفل ردیف scoped، timestamp با timezone و قید unique با `NULLS NOT DISTINCT`.
- ارتقا از وضعیت `leave.0006_entitlement_ledger` با دادهٔ آزمایشی به آخرین migration — موفق؛ backfill دو رخداد ledger ساخت و `reconcile_entitlement` برابر با `ok=True` بود (base=20، carry=2، adjustment=-1، reserved=2).
- در آزمون جداگانهٔ ارتقای PostgreSQL از `leave.0006`، دو `AuditEvent` تاریخی با شناسه‌های `123` و `leave-requests` بدون تغییر مقدار یا حذف رکورد به آخرین schema رسیدند.
- در PostgreSQL، نوع `entity_id` برابر `character varying` و `created_at` برابر `timestamp with time zone` است. جدول AuditEvent ایندکسی روی `entity_id` ندارد؛ cast در طرف کلیدهای اصلی زیرپرس‌وجو است و ستون AuditEvent cast نمی‌شود.
- اجرای اولیهٔ کل مجموعه پیش از رفع ناسازگاری قفل، ۱۹ خطای `FOR UPDATE is not allowed with DISTINCT clause` داشت. حذف `DISTINCT` غیرضروری از querysetهای فیلترشدهٔ مدل‌های پایه این خطا را رفع کرد؛ گروه ۴۹ تست مرتبط و سپس اجرای نهایی کامل، موفق شدند.
- هیچ پیکربندی CI در مخزن موجود نیست؛ workflow جدیدی اضافه نشد. راهنمای اجرای محلی: `docs/postgresql-validation-fa.md`.

## به‌روزرسانی Phase E (۱ نوامبر ۲۰۲۶)

- مجموعه کامل Django روی SQLite: `ZSH_ENV=test python manage.py test` — ۱۷۱ تست، موفق؛ ۳ تست PostgreSQL-only رد شدند. هشدار SQLite درباره `nulls_distinct` از محدودیت شناخته‌شده backend است.
- مجموعه کامل Django روی PostgreSQL 16: `ZSH_ENV=dev ... python manage.py test --keepdb` — ۱۷۱ تست، موفق؛ بدون ردشدن.
- تست‌های دفترکل روی PostgreSQL: رزرو و تأیید هم‌زمان، idempotency، triggerهای append-only و projection، reconciliation، انتقال/انقضا، برگشت و گردش‌کار تأیید/لغو موفق شدند.
- مهاجرت و backfill روی PostgreSQL از داده‌های pre-ledger اجرا شد: projection سهمیه ۲۰ + ۲ − ۱، رزرو ۲، مصرف ۳، مانده قابل استفاده ۱۶؛ reconciliation برابر بود.
- `python manage.py makemigrations --check --dry-run` — بدون migration جاافتاده.

## اجرای پایه سابق (۲۸ اکتبر ۲۰۲۶)

محیط: Python 3 با کتابخانه استاندارد (`unittest`)
دستور: `cd backend && python3 -m unittest discover -s tests -t .`

## اجرا شده
| مجموعه | تعداد تست | نتیجه |
|--------|-----------|-------|
| test_balance | 10 | OK |
| test_exports_imports_reports | 19 | OK |
| test_jalali | 8 | OK |
| test_permissions | 14 | OK |
| test_policy | 15 | OK |
| test_settings_holidays | 18 | OK |
| test_workdays | 7 | OK |
| test_workflow | 14 | OK |
| **جمع** | **105** | **OK** |

رویدادهای ثبت‌شده حین کار: (۱) باگ واقعی `from_gregorian` در لبه بازه توسط تست رفت‌وبرگشت کشف و رفع شد؛ (۲) یک خطای انتظار دستی در تست سیاست (آبان تا اسفند ۵ ماه است) اصلاح شد — کد نیاز به تغییر نداشت.

مواردی که این تست‌ها واقعاً اثبات می‌کنند (فقط در سطح منطق خالص): تست نشت «Canary» — مقدار نوع/دلیل/یادداشت محرمانه در خروجی serialize، تقویم و متن اعلان برای هیچ نقش غیرمجاز (کارمند، مدیر، همکار، جانشین، مدیر سامانه، گزارش‌خوان ارشد، HR شرکت دیگر) ظاهر نشد، حتی با روشن بودن تنظیمات افشا؛ خنثی‌سازی Formula Injection در CSV و XLSX (با بازخوانی فایل XLSX واقعی)؛ ورود گروهی همه‌یاهیچ با گزارش خط به خط.
**هنوز اثبات نشده:** اعمال همین قواعد در لایه API/Serializer/Export واقعی؛ آن‌ها باید از همین توابع عبور کنند و تست یکپارچه خودشان را لازم دارند.

## موارد خارج از Phase E که همچنان اجرا نشده‌اند
- E2E فرانت‌اند، ایمیل، Jobهای عملیاتی و آزمون واقعی backup/restore در استقرار.
- تست نشت محرمانگی و کنترل مجوز در تمام مسیرهای API/Export واقعی.

## خارج از دامنه Phase E
رابط/API مدیریت سهمیه، اجرای زمان‌بندی‌شده carry-forward/expiration، Frontend و استقرار/عملیات محصول.

## محدودیت‌های اعتبار
- تبدیل شمسی با قاعده چرخه ۳۳ساله و فقط برای ۱۳۴۳–۱۴۷۲؛ با تقویم رسمی کامل مقایسه نشده (فقط نوروز ۱۳۹۹–۱۴۰۵ و کبیسه‌های شناخته‌شده).
- الگوهای کاری در تست‌ها داده آزمون‌اند، نه سیاست سازمان.
- تفسیر قانونی (شمول جمعه‌ها در مرخصی) نیازمند تأیید حقوقی است (OQ-18).
