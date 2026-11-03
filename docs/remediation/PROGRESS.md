# پیشرفت پروژه اصلاح

## ممیزی نهایی مستقل — ۲ نوامبر ۲۰۲۶

این نتیجه برای checkout فعلی `main` در commit `9b82f59666398348664944cd04c72921e69594c9` ثبت شده است؛ worktree پیش از ممیزی تمیز بود. هیچ فایل Backend یا Frontend در این ممیزی تغییر نکرد؛ فقط اسناد remediation به‌روزرسانی شدند. بعضی snapshotهای قدیمی پایین‌تر حاوی ادعاهای متناقض‌اند و وضعیت جاری محسوب نمی‌شوند.

**F-04 status update:** سطر Authorization/scope در جدول زیر، snapshot تاریخی P20 است و برای سیاست چندنقشی superseded شده است. Option A (Union) با تأیید مالک سامانه تصویب و پیاده‌سازی شده؛ مرجع جاری [سیاست مجوزدهی](../authorization-policy.md) و [راستی‌آزمایی امنیتی](./SECURITY-VERIFICATION.md) است. آزمون API جدید روی SQLite موفق؛ PostgreSQL F-04 **NOT VERIFIED**.

آزمون کامل Backend روی SQLite در runner مجزای Docker: **exit 0، ۳۷۸ تست، ۱۴ skip مخصوص PostgreSQL**. روی PostgreSQL 17.11: **exit 0، ۳۷۸ تست، بدون skip**. آزمون‌های متمرکز امنیت/داده روی PostgreSQL: **exit 0، ۱۵۹ تست، بدون skip**. Django check، migration drift، compileall و production security check با ورودی ساختگی متنوع موفق شدند؛ migrate تازه همهٔ ۲۰ migration برنامه را اعمال کرد.

| Gate | وضعیت فعلی |
|---|---|
| Authorization/scope | **F-04 IMPLEMENTED; SQLITE API VERIFIED.** Union نقش‌های فعال در scope شرکت، با حفظ capability و فیلترهای محرمانگی؛ PostgreSQL F-04 **NOT VERIFIED**. متن P20 در این سطر تاریخی است. |
| Confidentiality | NOT VERIFIED طبق معیار سخت‌گیرانهٔ این audit: مسیرهای حساس دارای regression هستند، اما همهٔ مسیرهای API/export به‌صورت جداگانه با ماتریس scope کامل تأیید نشده‌اند. |
| Employment lifecycle | PASS برای end-date شامل همان روز، manager منقضی، activation و رد session در درخواست بعدی؛ ابطال پیش‌دستانهٔ session وجود ندارد. |
| Account activation | PASS برای token hash، single-use، انقضا، replay/concurrency و rate-limit در آزمون‌ها؛ provider ایمیل واقعی تأیید نشده است. |
| Data integrity | PASS در آزمون‌های hierarchy، Site، import، ledger، rollback و concurrency PostgreSQL. |
| PostgreSQL | PASS برای PostgreSQL 17.11 test؛ نه production DB |
| Audit append-only | NOT VERIFIED کلی: UPDATE/DELETE محافظت می‌شوند، اما TRUNCATE خام روی PostgreSQL حذف را ممکن کرد؛ محدودیت role production نامشخص است |
| Jobs | NOT VERIFIED در deployment؛ one-shot scheduler و heartbeat روی staging مصنوعی موفق، systemd timer واقعی ناموجود |
| Attachments | NOT VERIFIED در deployment؛ کد و hash restore تست شدند، volume/scanner production ناموجود |
| Backup/restore | restore مصنوعی روی PostgreSQL 17.11 موفق؛ backup/offsite/restore production **NOT VERIFIED** |

یافتهٔ عملیاتی تازه: DB تازه پس از migration جدول `zsh_cache` ندارد. درخواست API با production settings خطای 500 می‌دهد تا `manage.py createcachetable zsh_cache` اجرا شود؛ پس از آن محدودسازی ۲۰ درخواست/دقیقه در probe به 429 روی درخواست ۲۱ رسید. این provisioning در migration/deploy ثبت نشده است.

خروجی‌های مستقل: [امنیت](./SECURITY-VERIFICATION.md)، [شواهد PostgreSQL](./POSTGRESQL-EVIDENCE.md)، [عملیات Production](./PRODUCTION-OPERATIONS.md) و [شواهد آزمون](./TEST-EVIDENCE.md). وضعیت کل همچنان **NOT READY FOR PRODUCTION** است.

## وضعیت فاز در snapshot تاریخی — منسوخ

فاز ۳ — چرخه استخدام و offboarding: **BLOCKED / NOT VERIFIED**. بررسی مستقل نشان داد تغییر اولیه فقط مدل بود، بدون migration یا اتصال به مجوزها؛ آن تغییر ناقص حذف شد. هنوز سرویس مرکزی وضعیت استخدام، offboarding، rehire و regression test چرخه استخدام پیاده‌سازی نشده است. آخرین فازهای تکمیل‌شده: فاز ۰، فاز ۱، فاز ۲ و فاز مجوز کاتالوگ سراسری LeaveType.

## تکمیل‌های ثبت‌شده در snapshot تاریخی — شواهد قدیمی

- **فاز ۰ — Discovery/Baseline:** ساختار مخزن، branch/status، مستندات معماری/امنیت، فرمان‌های baseline و محدودیت PostgreSQL ثبت شد.
- **فاز ۱ — Critical privacy (سطح‌های بررسی‌شده):** confidential balance/entitlements، گزارش‌های executive و `coverage.awayCount` مدیر با regression tests فیلتر شدند. Gate حریم خصوصی کلی همچنان NOT VERIFIED.
- **فاز ۲ — HR absence:** endpoint به ایجاد مستقیم `APPROVED` تغییر کرد؛ approve دوم حذف شد؛ idempotency key الزامی و fingerprint کامل شد؛ تست event/audit، ledger، replay، scope و rollback افزوده شد.

## فایل‌های تغییر‌یافته در snapshot تاریخی

- `backend/leave/frontend_api_views.py`
- `backend/leave/authorization.py`
- `backend/leave/tests/test_frontend_api.py`
- `backend/leave/tests/test_workflow_api.py`
- `docs/api.md`
- `docs/api/api-contract.md`
- `docs/final-documentation/05-hr-user-guide.md`
- `docs/remediation/BASELINE.md`
- `docs/remediation/REMEDIATION-REPORT.md`
- `docs/remediation/SECURITY-AND-PERMISSIONS.md`
- `docs/remediation/WORKFLOW-AND-BUSINESS-RULES.md`
- `docs/remediation/TEST-EVIDENCE.md`
- `docs/remediation/PRODUCTION-GATE.md`
- `docs/remediation/KNOWN-LIMITATIONS.md`
- `docs/remediation/PROGRESS.md`

## آزمون‌های snapshot تاریخی — اجراهای جاری بالا جایگزین آن‌ها هستند

- `cd backend && DJANGO_SETTINGS_MODULE=config.settings_test python3 manage.py test`: ۱۸۱ آزمون، ۰ شکست، ۳ skipped (PostgreSQL-only).
- `cd backend && DJANGO_SETTINGS_MODULE=config.settings_test python3 manage.py check`: موفق.
- `cd backend && DJANGO_SETTINGS_MODULE=config.settings_test python3 manage.py makemigrations --check --dry-run`: موفق، تغییر مهاجرت ندارد.
- `cd frontend && npm test`: ۳۴ passed.
- `cd frontend && npm run build`: موفق؛ TypeScript و Vite.
- `git diff --check`: موفق.

## موانع snapshot تاریخی — برخی موارد با کد فعلی رفع شده‌اند

- چرخه استخدام هنوز در authorization اعمال نمی‌شود: در probe، پروفایل با `end_date=2026-10-31` و کاربر `is_active=True` در تاریخ 2026-11-01 همچنان نقش employee و manager داشت و مجاز به تصمیم‌گیری روی درخواست در انتظار بود. کاربر غیرفعال رد شد؛ این به‌تنهایی انقضای استخدام را پوشش نمی‌دهد.
- سیاست پیشنهادی برای پیاده‌سازی: `end_date` روز پایانی اشتغال و **شامل همان روز** است؛ تاریخ جاری باید از `timezone.localdate()` با timezone تنظیم‌شده (`Asia/Tehran`) گرفته شود. این سیاست هنوز در کد اجرا نمی‌شود.
- تعلیق/خاتمه صریح، سیاست درخواست‌های در انتظار هنگام offboarding، rehire با حفظ دوره‌های قبلی، و audit غیرقابل‌حذف هنوز پیاده‌سازی نشده‌اند.
- معماری احراز هویت فعلی فقط Django database session دارد؛ refresh token و API token یافت نشد. session version یا سرویس ابطال نشست در کد وجود ندارد. محدودکردن دسترسی در هر درخواست شدنی است، اما invalidation واقعی نشست‌های موجود باید جداگانه طراحی و آزموده شود.
- PostgreSQL در این محیط validate نشد؛ `pg_isready` نصب نیست و تست‌ها SQLite هستند.
- scheduler وجود ندارد، Bradford endpoint placeholder است و export API در routeها یافت نشد.
- چرخه استخدام، activation، ممیزی کامل RBAC/audit، PostgreSQL compatibility و production deployment هنوز تکمیل/تأیید نشده‌اند.

## شواهد راستی‌آزمایی مستقل فاز ۳

- Diff اولیه فقط `session_version` و مدل بدون استفاده `EmploymentPeriod` را اضافه می‌کرد؛ هیچ migration تولید نشده بود. اجرای اولیه تست‌ها با ۲۶ خطای `OperationalError: table leave_employeeprofile has no column named session_version` شکست خورد. تغییر ناقص حذف شد تا schema و تست‌ها دوباره با هم سازگار باشند.
- `cd backend && DJANGO_SETTINGS_MODULE=config.settings_test python3 manage.py test leave.tests.test_authorized_services leave.tests.test_frontend_api leave.tests.test_workflow_api`: **exit 0، ۲۶ آزمون، ۰ شکست**.
- `cd backend && DJANGO_SETTINGS_MODULE=config.settings_test python3 manage.py test leave.tests.test_workflow_api.LeaveWorkflowApiTests.test_hr_absence_rejects_unauthorized_and_rolls_back_invalid_submission`: **exit 0، ۱ آزمون، ۰ شکست**؛ درخواست نامجاز و خارج از دامنه 403/404 و ورودی نامعتبر 422 گرفتند؛ تست تعداد درخواست، رویداد، audit و idempotency را صفر assert می‌کند.
- `cd backend && DJANGO_SETTINGS_MODULE=config.settings_test python3 manage.py check`: **exit 0**، بدون خطای check. Test runner اخطار شناخته‌شده SQLite برای `nulls_distinct` در `PublicHoliday` نشان داد.
- `cd backend && DJANGO_SETTINGS_MODULE=config.settings_test python3 manage.py makemigrations --check --dry-run`: **exit 0**، تغییری شناسایی نشد.
- probe موقت با SQLite موقت و حذف فایل پس از اجرا: employee مجاز فعلی `True`؛ employee منقضی `True`؛ manager منقضی `True`؛ اختیار تصمیم مدیر منقضی `True` (bypass تأییدشده)؛ کاربر `is_active=False` رد شد؛ probe هیچ تغییری در وضعیت درخواست `PENDING` ایجاد نکرد.
- `git diff --check`: **exit 0**.

نتایج بالا صحت lifecycle را تأیید نمی‌کنند؛ برعکس، bypass را بازتولید می‌کنند. فاز ۳ تا پیاده‌سازی سرویس/مجوز، migration/backfill در صورت نیاز، سیاست درخواست‌های در انتظار و regression testهای کامل باید باز بماند.
