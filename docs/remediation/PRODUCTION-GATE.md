# دروازه آمادگی تولید

## نتیجه ممیزی مستقل — ۲ نوامبر ۲۰۲۶

وضعیت جاری checkout در commit `9b82f59666398348664944cd04c72921e69594c9` ثبت شده است. Gate 1 Authorization/scope و Gate 2 Confidentiality **NOT VERIFIED** طبق معیار این audit؛ Gate 3 lifecycle، Gate 4 activation و Gate 5 data integrity **PASS** در آزمون‌های ثبت‌شده؛ Gate 6 audit **NOT VERIFIED** به علت TRUNCATE و role grants؛ Gate 7 PostgreSQL **PASS** روی PostgreSQL 17.11 آزمایشی؛ Gate 8 jobs، Gate 9 attachments و Gate 10 operations **NOT VERIFIED** در deployment. جدول‌های تاریخی پایین‌تر snapshotهای قبلی‌اند و نتیجهٔ جاری نیستند. یافتهٔ جدید عملیات: دیتابیس تازه بدون جدول `zsh_cache` به API خطای 500 می‌دهد تا cache table دستی provision شود. نتیجهٔ کلی **NOT READY FOR PRODUCTION** است.

## Snapshotهای تاریخی — منسوخ

هیچ gate کلی با این تغییرات PASS اعلام نمی‌شود. نتیجه‌های جدول زیر متعلق به snapshot قبلی و پیش از آزمون PostgreSQL/lifecycle جاری است؛ برای تصمیم فعلی از گزارش نهایی Backend استفاده کنید.

| Gate | وضعیت | شاهد/مانع |
|---|---|---|
| A — حریم خصوصی | NOT VERIFIED کلی | balance، entitlements، executive reports و coverage manager آزموده و اصلاح شدند؛ ممیزی همه timeline/notification/exportها انجام نشده است. |
| B — Authorization | NOT VERIFIED | scopeهای balance/HR absence آزموده شدند؛ ماتریس تمام endpointها، site/department و global configuration باقی است. |
| C — Workflow | PARTIAL / FIXED AND VERIFIED برای HR absence | ایجاد مستقیم approved، ledger، event، idempotency و rollback آزموده شد؛ همه transitionها/هم‌زمانی workflow بازبینی کامل نشده‌اند. |
| D — Employee lifecycle | NOT VERIFIED | end_date، suspension، session revocation و rehire فاز بعدی است. |
| E — Account activation | NOT VERIFIED | invitation و password setup هنوز بررسی/پیاده‌سازی نشده است. |
| F — Data integrity | PARTIAL | migration drift و آزمون‌های SQLite موفق‌اند؛ hierarchy/import/append-only کامل بررسی نشده است. |
| G — PostgreSQL | BLOCKED / NOT VERIFIED | SQLite تست شد؛ ۳ آزمون PostgreSQL-only رد شدند؛ اتصال واقعی PostgreSQL برقرار/تأیید نشد. |
| H — Frontend | NOT VERIFIED کلی | ۳۴ تست و production build موفق؛ بازبینی کامل API contracts/role UX/E2E انجام نشده است. |
| I — Jobs | IMPLEMENTED / NOT VERIFIED IN PRODUCTION | مدیریت اجرای DB-backed، retry محدود، readiness، API وضعیت و نمونه systemd timer وجود دارد؛ نصب timer، پیکربندی سیاست‌ها و آزمون PostgreSQL/production هنوز انجام نشده است. |
| J — Deployment | NOT VERIFIED | static prod settings بررسی اولیه شده؛ TLS proxy، secrets در محیط واقعی، backup/restore و monitoring اجرا/تأیید نشده‌اند. |
| K — Testing | PARTIAL | آزمون‌های موجود سبز؛ PostgreSQL/E2E و lint مستقل در دسترس نبودند. |

## پیش‌نیازهای محیط production

`config.settings_prod` به `ZSH_SECRET_KEY` با حداقل ۵۰ نویسه، `ZSH_ALLOWED_HOSTS` غیرخالی، `ZSH_DB_PASSWORD` و PostgreSQL نیاز دارد. `DEBUG=False`، cookie امن، redirect HTTPS و HSTS در تنظیمات دیده می‌شوند. پیکربندی reverse proxy، trusted forwarded headers، CORS، سرویس ایمیل، backup، restore، alerting و scheduler باید پیش از release در محیط مقصد تعریف و آزموده شوند.

## نتیجه

**NOT READY FOR PRODUCTION.** این تغییرات دو نقص مهم را با آزمون اصلاح می‌کنند، اما Gateهای A/B/D/E/G/I/J و تکمیل آزمون‌های production هنوز بسته نشده‌اند.
