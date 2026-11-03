# امنیت و حریم خصوصی

## ۱. دارایی‌ها و تهدیدها
| دارایی | تهدید | کنترل |
|--------|-------|-------|
| نوع/دلیل/سند محرمانه | افشا به مدیر/همکار/گزارش‌خوان، نشت در API، تقویم، اعلان، خروجی، لاگ | تک‌نقطه redaction؛ ایزوله‌سازی در همه سطوح؛ تست نشت خودکار |
| درخواست‌های دیگران | IDOR | scope در selectors؛ 404 برای خارج از scope؛ تست ماتریسی |
| مانده/سهمیه | دوبار کسر، race | `select_for_update`، تراکنش، قیود DB، idempotency |
| حساب‌ها | brute force، سرقت نشست | Argon2، Rate Limit، قفل موقت، کوکی Secure/HttpOnly/SameSite، انقضای نشست |
| فایل‌ها | اجرای فایل، دانلود غیرمجاز، فایل مخرب، دسترسی میان‌شرکتی | اعتبارسنج ساختاری PDF/PNG/JPEG، نام تصادفی، storage خصوصی با permission محدود، دانلود scopeشده از API، `attachment`، `nosniff` و no-store |
| خروجی‌ها | CSV/Formula Injection | پیشوند `'` برای سلول‌های شروع با `= + - @ \t \r` در CSV و XLSX |
| لاگ‌ها | متن آزاد حساس (SEC-003) | بدون ثبت دلیل/یادداشت پزشکی در لاگ؛ AuditEvent با فیلدهای redacted؛ دسترسی محدود |
| زنجیره تأمین | وابستگی آسیب‌پذیر (SEC-006) | قفل نسخه، `pip-audit`، `npm audit`، اسکن Secret، SBOM در CI |
| Job | اجرای تکراری/ناموفق بی‌صدا | idempotency + BackgroundJobExecution + Health هشدار |

## ۲. کنترل‌ها
CSRF برای نشست؛ CORS بسته؛ ORM (بدون SQL خام)؛ escaping پیش‌فرض React و CSP؛ اعتبارسنجی ورودی در Serializer؛ هدرهای HSTS؛ Secrets فقط از Environment؛ کاربر DB با حداقل دسترسی؛ Backup رمزگذاری‌شده؛ ثبت رویداد امنیتی (ورود ناموفق، رد مجوز، تغییر نقش).

مجوزهای فعلی در سمت سرور و از طریق `RoleAssignment` و scopeهای سازمانی حل می‌شوند؛ نقش‌های فرانت‌اند صرفاً UX هستند. selectorهای ORM برای داده‌های فردی، scope مجاز را قبل از دسترسی به رکورد اعمال می‌کنند. مدیر سامانه و گزارش‌خوان به رکورد مرخصی افراد دسترسی ندارند. آزمون‌های Phase C در `backend/tests/test_rbac.py` شامل cross-company، IDOR، privilege escalation و canary محرمانه است. مسیرهای عملیاتی درخواست/تأیید مرخصی هنوز پیاده‌سازی نشده‌اند و باید از همان موتور مجوز استفاده کنند.

## ۳. سیاست محرمانگی
- نوع محرمانه: ثبت فقط HR؛ همه دیگران «غایب» (تاریخ و وضعیت، بدون نوع/دلیل/یادداشت/نظر/تاریخچه).
- فهرست انواع مرخصی برای employee/manager فقط انواع فعال و قابل درخواست را دارد؛ HR دامنه‌دار فقط انواع فعال را برای عملیات می‌خواند، و admin سراسری همه انواع را می‌بیند. هیچ‌یک از این سطوح خواندن به‌تنهایی مجوز تغییر نیست؛ نوشتن کاتالوگ global نیازمند `leave_type.global.manage` است.
- `LeaveType` مدل سراسری و بدون FK شرکت است. HR scoped نمی‌تواند آن را ایجاد/ویرایش/غیرفعال کند؛ عملیات مجاز admin نیازمند دلیل است و با actor، before/after، زمان و correlation ID اختیاری ثبت می‌شود.
- اعلان‌ها و ایمیل هرگز نوع/دلیل محرمانه ندارند.
- جانشین دلیل/اطلاعات پزشکی را نمی‌بیند.
- نقش گزارش‌خوان ارشد: فقط شمارش تجمیعی با حداقل اندازه گروه (k-anonymity، پیش‌فرض ۵ — نیازمند تأیید) تا فرد قابل شناسایی نشود.
- Bradford Factor فقط HR، با هشدار «ابزار تحلیلی، نه مبنای تصمیم».

## ۴. آنچه هنوز تأیید نشده
موارد baseline بالا تا اجرای تست‌های مربوطه «اثبات‌شده» محسوب نمی‌شوند. پیاده‌سازی و آزمون‌های محدودسازی نرخ در بخش ۵ تکمیل شده‌اند؛ اشتراک cache و تنظیم proxy در محیط واقعی همچنان نیازمند راستی‌آزمایی است.

## ۵. محدودسازی نرخ API

DRF نسخه 3.18.3 نصب‌شده در زمان بررسی، rateها را بدون `DEFAULT_THROTTLE_CLASSES` فعال نمی‌کند. محدودسازی اکنون به‌صورت سراسری برای IP ناشناس، کاربر احراز هویت‌شده و IP کاربر احراز هویت‌شده فعال است؛ endpointهای ورود، فعال‌سازی، عملیات گذرواژه، دعوت، جست‌وجوی کارکنان، ورود گروهی، گزارش‌های پرهزینه و upload نیز scope جدا دارند. ورود علاوه بر IP، بر اساس نام کاربری نرمال‌شده و هش‌شده محدود می‌شود. هیچ نام کاربری، گذرواژه، توکن یا IP خام در لاگ محدودسازی ثبت نمی‌شود. پاسخ محدودسازی همواره HTTP 429 با `code=rate_limited` و هدر `Retry-After` است.

هدر `X-Forwarded-For` تنها وقتی پذیرفته می‌شود که `REMOTE_ADDR` در شبکه‌های `ZSH_TRUSTED_PROXY_IPS` قرار داشته باشد. این متغیر باید فقط CIDRهای proxyهای تحت کنترل را شامل کند؛ proxy باید header ورودی کاربر را حذف/بازنویسی یا زنجیره را به‌درستی append کند، و backend نباید از اینترنت عمومی قابل دسترسی باشد. بدون proxyهای پیکربندی‌شده، هدر نادیده گرفته می‌شود.

در production، `SECURE_SSL_REDIRECT` فعال و `SECURE_PROXY_SSL_HEADER` برابر `HTTP_X_FORWARDED_PROTO=https` است. middleware پیش از `SecurityMiddleware` مقدار `X-Forwarded-Proto` را حذف می‌کند مگر آنکه `REMOTE_ADDR` در `ZSH_TRUSTED_PROXY_IPS` باشد و مقدار هدر دقیقاً `https` باشد. این مانع اعتماد به هدر client عمومی می‌شود؛ اما proxy همچنان باید مقدار را بازنویسی کند و دسترسی مستقیم عمومی به upstream باید در شبکه بسته باشد. با فهرست CIDR خالی، forwarded header از هیچ peerی trusted نیست؛ TLS مستقیم تا WSGI همچنان با `wsgi.url_scheme` امن کار می‌کند. در معماری TLS-termination، CIDR peerهای واقعی الزامی است تا redirect loop رخ ندهد.

`ZSH_CSRF_TRUSTED_ORIGINS` در production فقط originهای دقیق HTTPS بدون wildcard یا path می‌پذیرد. `/api/v1/health/` و `/api/v1/ready/` را از سمت proxy روی HTTPS probe کنید؛ probe مستقیم HTTP به upstream عمداً redirect می‌شود. توپولوژی reverse proxy واقعی در این repository تعریف نشده است؛ راهنمای [استقرار](./deployment.md) الگوی بی‌طرف نسبت به محصول و تصمیم‌های لازم را ثبت می‌کند. تست‌های تنظیمات/هدر، redirect، Host، HSTS، cookie و CSRF در `backend/tests/test_production_proxy_security.py` هستند؛ آزمون محیط واقعی شبکه و گواهی باید در staging اجرا شود.

`settings_prod.py` از `DatabaseCache` روی دیتابیس PostgreSQL مشترک استفاده می‌کند، نه حافظه محلی process. پیش از شروع Web Workerها، مرحلهٔ release باید پس از migration فرمان `python manage.py provision_cache_table --settings=config.settings_prod` را با کاربر سرویس اجرا کند؛ این فرمان تکرارپذیر است و ایجاد هم‌زمان جدول را با advisory lock سریال می‌کند. جزئیات اجرای systemd در [راهنمای استقرار](./deployment.md) است. همهٔ workerها باید از همان دیتابیس/جدول استفاده کنند. DRF throttling مبتنی بر cache برای نرخ‌گذاری تقریبی است و به‌تنهایی کنترل اتمیک/لبه‌ای در برابر حمله توزیع‌شده محسوب نمی‌شود.

محدودسازی فقط درخواست‌های HTTP/DRF را دربرمی‌گیرد؛ اجرای مستقیم سرویس‌ها در commandها یا jobهای پس‌زمینه throttle نمی‌شود. در حال حاضر endpoint جداگانه‌ای برای password reset، token refresh یا export فایل وجود ندارد؛ scopeهای `password_reset` و `expensive_operation` آماده‌اند تا در صورت افزودن endpoint استفاده شوند. مسیر گزارش فعلی داده JSON می‌دهد؛ upload و download پیوست scopeهای فایل مستقل دارند. عملیات HTTP معتبر با حساب‌های سرویس احتمالیِ آینده باید قبل از استفاده، سیاست مستقل و صریح داشته باشند.

آزمون‌ها رفتار anonymous/user/IP، ورود و عدم افشای وجود حساب، فعال‌سازی، دعوت، password operation، bulk import، جست‌وجو، گزارش، upload، پاسخ 429، `Retry-After`، scope مربوط به reset و اعتماد به proxy را پوشش می‌دهند. آزمون تنظیمات محلی به‌تنهایی اشتراک cache چند-process در deployment را اثبات نمی‌کند.

## ۶. ماتریس تغییرناپذیری تاریخچه و حسابرسی

| جدول | طبقه‌بندی | رفتار و دلیل |
|---|---|---|
| `AuditEvent` | فقط append | ثبت رویدادهای حسابرسی؛ فقط ایجاد رکورد جدید. |
| `LeaveRequestEvent` | فقط append | گردش وضعیت و اصلاحات با رویداد تازه ثبت می‌شوند، نه ویرایش رویداد قدیمی. |
| `LeaveRequestComment` | فقط append | پیام تازه افزوده می‌شود؛ مسیر ویرایش/حذف یا استثنای redaction تأییدشده‌ای در سامانه نیست. |
| `CalendarHolidayHistory` | فقط append | snapshot هر ایجاد، تغییر یا غیرفعال‌سازی تعطیلی. |
| `PublicHolidayHistory` | فقط append | snapshot هر ایجاد، تغییر یا غیرفعال‌سازی تعطیلی؛ بدون FK باقی می‌ماند تا سابقه پس از غیرفعال‌سازی محفوظ باشد. |
| `EntitlementEvent` | فقط append | دفترکل تخصیص؛ اصلاح با رویداد معکوس/جدید انجام می‌شود. محافظ DB قبلی در migration `0008` حفظ شده است. |
| `LeavePolicyVersion` | فقط append | نسخه مؤثر سیاست با ثبت نسخه جدید اصلاح می‌شود؛ مدل از قبل ویرایش/حذف را رد می‌کند و اکنون DB نیز آن را enforce می‌کند. |
| `LeaveRequest` | رکورد عملیاتی mutable | وضعیت، تصمیم و اصلاح درخواست در سرویس workflow تغییر می‌کند؛ رویدادهای مرتبط در `LeaveRequestEvent`/`AuditEvent` ثبت می‌شوند. |
| `CalendarHoliday` | رکورد عملیاتی mutable | تغییر/غیرفعال‌سازی مجاز و در history ثبت می‌شود. |
| `PublicHoliday` | رکورد عملیاتی mutable | ویرایش و غیرفعال‌سازی مجاز و در history ثبت می‌شود؛ حذف فیزیکی مسیر عادی نیست. |
| `EmployeeInvitation` | رکورد چرخه‌عمر mutable | وضعیت ارسال/مصرف/ابطال تغییر می‌کند و رویدادهای حساس Audit می‌شوند. |
| `RoleAssignment` | رکورد عملیاتی mutable | ابطال به‌صورت تغییر وضعیت مجاز است و در Audit ثبت می‌شود. |
| `BradfordNote` | رکورد عملیاتی mutable | `update_or_create` ویرایش یادداشت جاری را پشتیبانی می‌کند. |
| `WorkPattern`, `ContractPeriod` | داده عملیاتی/مؤثر | مدل‌ها رکوردهای جاری یا بازه‌ای هستند؛ قرارداد تصحیح/نسخه‌بندی کامل برای آن‌ها تعریف نشده، پس trigger تغییرناپذیری اعمال نمی‌شود. |
| `SystemSetting`, `EmployeeProfile`, `LeaveType` | داده عملیاتی mutable | منبع وضعیت جاری؛ تغییرهای پشتیبانی‌شده در سرویس‌ها Audit می‌شوند. |
| `LeaveEntitlement` | projection دفترکل | مانده قابل‌نمایش از رویدادهای `EntitlementEvent` مشتق می‌شود؛ trigger projection موجود فقط تغییرهای ثبت‌نشده را رد می‌کند. |
| `NotificationRead` | وضعیت مشتق‌شده و mutable | نشانگر خوانده‌شدن اعلان است، نه سابقه تجاری. |
| `WorkflowIdempotency` | داده عملیاتی کوتاه‌عمر | برای replay/idempotency است، نه منبع تاریخچه؛ retention آن مستقل است. |
| `LeaveRequestAttachment` | داده عملیاتی/مدرک | مسیر فایل و متادیتا؛ retention/redaction سیاست جداگانه می‌خواهد و در این تغییر immutable نشده است. |

Migration `0014` برای شش جدول append-only جدید، triggerهای `BEFORE UPDATE OR DELETE` در PostgreSQL و triggerهای متناظر در SQLite ایجاد می‌کند؛ محافظ `EntitlementEvent` در `0008` دست‌نخورده می‌ماند. هیچ ردیف موجودی در migration تغییر یا حذف نمی‌شود. محافظ DB رد کردن `QuerySet.update/delete`, `bulk_update` و SQL مستقیم را پوشش می‌دهد؛ `bulk_create` و درج عادی همچنان مجازند. تست‌ها تمام هفت جدول را از مسیر مدل، QuerySet، bulk update و SQL مستقیم بررسی می‌کنند. آزمون اختصاصی PostgreSQL در صورت اجرای suite روی PostgreSQL فعال است؛ اجرای SQLite به‌تنهایی مدرک اجرای PostgreSQL نیست.

برای پیوست‌ها، فقط فایل فعال با `scan_status=clean` از endpoint دارای کنترل scope دانلود می‌شود؛ نبود/خطای اسکنر fail-closed است و Production بدون `ZSH_ATTACHMENT_SCANNER_COMMAND` بالا نمی‌آید. فایل‌ها خارج از مسیر public ذخیره و با نام تصادفی نگهداری می‌شوند. سیاست retention فعلی دو ماه از بارگذاری است؛ حذف خودکار نیست و purge فقط با تأیید صریح، دلیل Audit و رعایت legal hold انجام می‌شود. اسکنر واقعی و زیرساخت Production هنوز **NOT VERIFIED** هستند. جزئیات rollout/retention در [سیاست امنیت و نگهداری پیوست](./attachment-security-fa.md) است. سایر تاریخچه‌های append-only retention جداگانه دارند.

در production، `ZSH_DB_USER` دیگر به `postgres` پیش‌فرض نمی‌شود و باید صریح تنظیم شود. نقش runtime برنامه نباید مالک schema/table یا superuser باشد و نباید مجوز `CREATE`, `ALTER`, `DROP`, `TRIGGER`, `TRUNCATE` یا غیرفعال‌کردن triggerها داشته باشد. در صورت امکان برای هفت جدول append-only فقط `SELECT` و `INSERT` (به‌علاوه دسترسی لازم به sequence) بدهید و `UPDATE`, `DELETE`, `TRUNCATE`, `TRIGGER` را revoke کنید. اجرای migration باید با هویت جداگانه و محدود به فرایند deployment انجام شود؛ راستی‌آزمایی privilegeهای محیط واقعی هنوز لازم است.

شواهد آزمون این تغییر: migration و تست‌های مسیر SQL مستقیم روی PostgreSQL 17 اجرا شدند؛ کل backend نیز روی PostgreSQL 17 با خاموش‌کردن موقت `SECURE_SSL_REDIRECT` فقط برای Django test client اجرا شد: `227` آزمون، همگی موفق. کل backend روی SQLite نیز `227` آزمون را با موفقیت گذراند (`5` مورد skip مخصوص محیط). این شواهد صحت کد و triggerها را در محیط آزمون نشان می‌دهد، نه privilegeهای دیتابیس production.
