# چک‌لیست آمادگی تولید

**وضعیت کلی:** ⛔ تأیید تولید نشده. Build و suiteهای محلی با SQLite برای صدور مجوز Production کافی نیستند. هر مورد باید با artifact/خروجی محیط پیش‌تولید اثبات شود؛ وضعیت‌های «مسدود» به معنی نبود شواهد در repository فعلی است.

تعریف وضعیت: **انجام‌نشده/تأییدنشده** = شواهد ندیده‌ایم؛ **مسدود** = تنظیم/تصمیم/زیرساخت لازم موجود نیست؛ **نیازمند بازآزمایی** = بخشی آزموده شده، اما gate کامل نیست.

| ID | حوزه و معیار پذیرش قابل اثبات | وضعیت فعلی |
|---|---|---|
| SEC-01 | کلید `ZSH_SECRET_KEY` فقط از secret manager، >=۵۰ نویسه؛ production settings با کلید/host ناموجود fail-fast؛ هیچ secret در Git/Log نیست؛ scan گزارش پیوست دارد | نیازمند اجرای محیط؛ settings کد شرط کلید/host دارد، scan اجرا نشده |
| SEC-02 | HTTPS-only، redirect/HSTS روی origin واقعی؛ Cookie session/CSRF امن و Secure در مرورگر؛ TLS scan پذیرفته | تنظیمات و Django smoke tests برای trusted proxy/redirect/HSTS/cookie اضافه شد؛ مسدود برای گواهی، proxy و scan واقعی محیط |
| SEC-03 | CSRF برای همه mutationهای session-based تست شده؛ Origin/CSRF trusted دقیق و بدون wildcard تولید | originهای دقیق HTTPS در production enforce و نمونه CSRF با trusted/untrusted origin آزموده شد؛ آزمون همه mutationها و origin واقعی staging باقی است |
| SEC-04 | rate-limit login در edge+application، lockout/incident policy و audit failure؛ آزمون 429 مستند | throttle app و آزمون Django وجود دارد؛ edge/monitoring نامشخص |
| SEC-05 | RBAC ماتریس endpoint×role اجرا؛ cross-company، IDOR، manager snapshot، self-escalation و HR scope همگی رد می‌شوند | آزمون Django بخشی موفق؛ اجرای کامل مستقل production-like و security review لازم |
| SEC-06 | Canary در detail/calendar/notification/attachment/report/audit و export برای همه نقش غیرمجاز صفر بار دیده شود | helper tests و بخشی API tests وجود دارد؛ کل مسیرها/محیط واقعی اثبات نشده |
| SEC-07 | File upload محدود به ۱۰ MiB، PDF/PNG/JPEG با parser، storage خصوصی، دانلود scoped فقط برای clean، scanner fail-closed، retention و restore آزمون‌پذیر | تست Django برای validation، cross-company/employee access، headers، scanner unavailable/timeout/crash/invalid output، retry، symlink، orphan cleanup و retention/legal hold؛ Production بدون scanner command fail-fast است. Integration واقعی scanner، volume/backup و restore مقصد **NOT VERIFIED**؛ سیاست ۲ ماه باید HR/Legal تصویب کند. |
| DB-01 | PostgreSQL نسخه مصوب، TLS/credential least privilege، backup و اتصال production؛ health/readiness و query check پایدار | تنظیمات prod PostgreSQL است؛ سرویس/نسخه/اتصال deployment اثبات نشده |
| DB-02 | همه migrations ۰۰۰۱–۰۰۱۱ روی snapshot نزدیک تولید بدون drift، lock plan و rollback/forward plan اجرا شود | SQLite suite موفق؛ PostgreSQL migration rollout در این اجرا نشده |
| DB-03 | `makemigrations --check --dry-run` خروجی بدون تغییر؛ release migrationها در staging اجرا و audit شود | در این workspace با `settings_test` اجرا شد: `No changes detected`; PostgreSQL/staging rollout هنوز انجام نشده |
| LED-01 | projection ledger با sum(delta) برای همه entitlementها برابر؛ گزارش reconciliation بدون مورد؛ repair فقط با event جبرانی ممیزی‌شده | منطق/آزمون SQLite وجود دارد؛ reconciliation سراسری/فرمان عملیاتی وجود ندارد |
| LED-02 | update/delete مستقیم EntitlementEvent توسط DB رد؛ trigger migration روی PostgreSQL مشاهده و آزموده شود | model append-only و migrations trigger دارد؛ ۳ آزمون PostgreSQL در این اجرا skip شدند |
| LED-03 | دو رزرو موازی و دو approve موازی بدون lost update/دو posting در PostgreSQL تکرارپذیر | آزمون PostgreSQL نوشته شده؛ در محیط فعلی اجرا/تأیید نشده |
| LED-04 | import/adjustment و درخواست چندساله همه‌یا‌هیچ، idempotent و reason/audit قابل گزارش | سرویس و endpointها در کد موجودند؛ آزمون مستقیم API اصلاح/ورود گروهی و اجرای PostgreSQL در این ممیزی انجام نشده؛ با fixture disposable و تطبیق ledger/projection بازآزمایی شود |
| TEST-01 | دستور canonical backend `DJANGO_SETTINGS_MODULE=config.settings_test python3 manage.py test` سبز و گزارش نسخه/تعداد artifact شود | اجرا شد: ۱۷۶ مورد؛ ۱۷۳ موفق و ۳ skipped؛ SQLite هشدار قید `nulls_distinct` |
| TEST-02 | frontend `npm test` سبز؛ unit/E2E مرورگر، accessibility، مسیرهای role و خطای server نیز پوشش داده شوند | اجرا شد: ۳۴ موفق؛ E2E/مرورگر اجرا نشده |
| TEST-03 | `npm run build` خروجی تکرارپذیر و artifact deployment ثبت شود | در این اجرا موفق |
| TEST-04 | آزمون PG concurrency، trigger و migration در PostgreSQL تأیید شود | انجام‌نشده؛ سه تست با SQLite رد می‌شوند |
| TEST-05 | آزمون integration سازمانی شامل SMTP، storage، proxy و DB واقعی موفق | مسدود: service integration در مخزن/محیط تعریف نشده |
| UI-01 | UI هیچ قابلیت غیرفعال/ناسازگار نشان ندهد؛ ساعتی فقط پس از قرارداد server و persist واقعی فعال شود | blocker ثبت‌شده: فرم hourly موجود ولی policy false و API فیلدها را نمی‌پذیرد |
| UI-02 | هر endpoint client با method/path واقعی، status/schema و role خود contract test شود | بخشی در `test_frontend_api` موجود؛ جدول API سند ۰۱ موارد بدون wrapper را مشخص می‌کند |
| CFG-01 | `DJANGO_SETTINGS_MODULE`/WSGI/ASGI برای deploy واقعی resolved؛ process حتماً production settings را انتخاب کند و smoke test انجام شود | wrapper `config.settings` وجود دارد و با `ZSH_ENV` ماژول انتخاب می‌کند؛ مقدار پیش‌فرض development است. process واقعی production و smoke test deployment هنوز تأیید نشده |
| CFG-02 | تنظیمات dev هرگز در production؛ DEBUG false، allowed hosts دقیق، CSRF origins دقیق، secret rotation runbook | كد production checks دارد؛ process/rotation نامشخص |
| CFG-03 | تنظیمات DB مستند و با `.env.example` هماهنگ؛ اتصال اشتباه fail-fast | `ZSH_DB_ENGINE` در settings پایه و `.env.example` مصرف می‌شود؛ `ZSH_USE_SQLITE=1` override اضافی توسعه است. dotenv خودکار بارگذاری نمی‌شود و مقادیر عملیاتی باید در محیط process تعریف شوند |
| DEP-01 | manifest deploy، pinned runtime/dependency، static/media persistence، health probes و rollout/rollback تأییدشده | مسدود: Docker/Compose/CI/CD/deploy manifest یافت نشد |
| DEP-02 | TLS، DNS، reverse proxy header، timeout/body limit و static/media route در staging آزموده | topology/proxy واقعی در مخزن نیست؛ الگوی امن header، آزمون Django و smoke commands اضافه شد. انتخاب proxy/TLS، بستن direct upstream و آزمون staging هنوز مسدودند |
| OPS-01 | `pg_dump` رمزگذاری‌شده، retention/immutability، فایل پیوست همراه DB؛ زمان آخرین backup و alert قابل مشاهده | انجام‌نشده: ابزار/job/سیاست repo ندارد |
| OPS-02 | restore drill روی محیط جدا؛ DB و فایل پیوست restore و تعداد رکورد/ledger reconciliation برابر با baseline | انجام‌نشده؛ گزارش restore موجود نیست |
| OPS-03 | مانیتورینگ DB، 5xx، 401/403/429، ظرفیت disk/media، backup و job؛ آستانه و on-call ثبت شود | مسدود: monitoring/alerting تعریف نشده؛ jobs endpoint خالی |
| OPS-04 | incident response شامل نشت داده، compromise session، خطای migration/ledger و بازیابی؛ tabletop ثبت‌شده | انجام‌نشده |
| OPS-05 | لاگ‌ها به سامانه امن با دسترسی محدود، retention، زمان‌بندی و redaction منتقل؛ no secrets/PII | کد فقط console logger دارد؛ ingestion/retention بیرونی نامشخص |
| BIZ-01 | HR/Legal تأیید انواع، تقویم، سال entitlement، accrual، carry-forward، قواعد گذشته‌نگر و محرمانگی | مسدود به تصمیم سازمان؛ schema تنها implementation است نه تأیید حقوقی |
| BIZ-02 | HR و مدیران آموزش دیده، role mapping/کارمند اولیه و provisioning/reset credential مستند شده | مسدود: کاربر جدید password unusable؛ UI invitation/reset ندارد |
| BIZ-03 | Owner برای role assignment API-only، گزارش، export، site configuration و پشتیبانی تعیین شود | نیازمند تصمیم/مسئول؛ صفحه UI نقش وجود ندارد |
| DOC-01 | مستندهای Persian با نسخه release بازبینی، لینک‌ها/routeها/دستورات اعتبارسنجی و owner مشخص | در این ممیزی لینک‌های داخلی و فرمان‌های منتخب بررسی و چند ادعای ناسازگار اصلاح شد؛ sign-off مالک محصول و نگاشت نسخه انتشار هنوز لازم است |
| UAT-01 | کارمند، مدیر، HR، مدیر سامانه و Executive سناریوهای پذیرش با داده مصنوعی امضا کنند | انجام‌نشده؛ این گزارش UAT نیست |
| SIGN-01 | امنیت، DB، HR/Legal، عملیات، مالک محصول و business owner امضای release با تاریخ/نسخه داشته باشند | انجام‌نشده؛ امضاکننده/تصمیم‌گیر باید مشخص شود |

## Gate نهایی آزادسازی

انتشار فقط زمانی قابل بررسی است که **هیچ SEC/DB/LED/DEP/OPS blocker باز نماند**، آزمون PostgreSQL و restore واقعی مستند باشد، ساعتی/Job/identity provisioning تکلیف داشته باشد، UAT نقش‌ها امضا شود و نسخه build/dependency/DB ثبت گردد. عبور خودکار از checklist یا build موفق به‌تنهایی تایید انتشار نیست.

## سوال‌های باز برای مالک محصول/عملیات

۱. سال و قواعد entitlement شمسی/میلادی، accrual/carry-forward و expire کدام است و چه کسی مالک آن است؟
۲. آیا مرخصی ساعتی لازم است؟ اگر بله، قرارداد API/ذخیره‌سازی و قواعد آن باید قبل از روشن‌شدن UI تکمیل شود.
۳. آیا Job، email/push، auto-cancel و reminder لازم است؟ scheduler/worker و مقصد اعلان کدام‌اند؟
۴. منبع هویت، provisioning برای حساب‌های `unusable_password` و فرایند reset چیست؟
۵. سازمان برای backup، نگهداری فایل پزشکی، retention و restore چه RPO/RTO و کلیدگذاری می‌خواهد؟
۶. نقش‌های Executive باید کدام گزارش‌های تجمیعی را ببیند و چه حداقل اندازه گروهی اعمال شود؟
۷. چه کسی مالک تنظیم `ZSH_ENV=production`, WSGI/ASGI, secrets و pipeline انتشار خواهد بود؟
