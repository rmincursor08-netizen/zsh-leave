# ارزیابی آمادگی انتشار پس از P20

**تاریخ ارزیابی:** ۲ نوامبر ۲۰۲۶

**یادداشت superseded:** این سند snapshot تاریخی P20 است. یافتهٔ آن دربارهٔ نامشخص بودن سیاست HR+Manager با تصمیم مصوب Option A (Union) و پیاده‌سازی F-04 superseded شده است؛ مرجع جاری [سیاست مجوزدهی](./authorization-policy.md) و [وضعیت راستی‌آزمایی امنیتی](./remediation/SECURITY-VERIFICATION.md) است. این تغییر سایر یافته‌های انتشار P20 را رفع یا تأیید نمی‌کند.

**مخزن/نسخه:** `biwagidaa-droid/zsh-leave`، شاخهٔ `main`، HEAD برابر `c3cb7f360ba76a9a0935325c85a7af4e5fcc7a14` (`P20`)
**دامنه:** بررسی Backend، Frontend، شواهد P20 و مستندات استقرار؛ بدون تغییر کد یا ایجاد/اجرای Migration دستی.

## مبنای شواهد و نحوهٔ خواندن وضعیت

- یافته‌های این سند بین «در آزمون مشخص تأییدشده»، «در کد موجود ولی در محیط مقصد تأییدنشده» و «حل‌نشده» تفکیک می‌شوند. NOT VERIFIED به معنی سالم بودن نیست.
- P20 در تاریخچه، فقط مستندات remediation را تغییر داده است؛ کد runtime در این commit تغییر نکرده است. گزارش P20 عمدتاً نتایج checkout قبلی `9b82f59666398348664944cd04c72921e69594c9` را بازتاب می‌دهد.
- تاریخچهٔ موجود commitهای P01 تا P18 و P20 را دارد، اما commit یا برچسب صریح P19 در `git log --all` یافت نشد. بنابراین نتیجهٔ مستقل برای یک «بستهٔ P19» قابل انتساب نیست؛ وضعیت قابلیت‌های فعلی از درخت کد و آزمون‌های موجود ارزیابی شده است.
- برخی مستندات، snapshotهای قدیمی و متناقض دارند. برای وضعیت جاری، بخش‌های «ممیزی نهایی مستقل — ۲ نوامبر ۲۰۲۶» در اسناد remediation را مبنا قرار دهید، نه جدول‌های صریحاً تاریخی/منسوخ همان فایل‌ها.
- این ارزیابی هیچ سرویس Production یا داده/اعتبارنامهٔ واقعی را لمس نکرده است.

### اجراهای انجام‌شده در این ارزیابی

| فرمان/شاهد | نتیجهٔ مشاهده‌شده | محدودیت |
|---|---|---|
| `docker run ... python:3.14-slim ... DJANGO_SETTINGS_MODULE=config.settings_test python manage.py test --verbosity 1` | **اجرا شد؛ exit 0؛ ۳۷۸ تست، ۱۴ skip، صفر شکست.** | ۱۴ تست مختص PostgreSQL skip شدند. SQLite هشدار `models.W047` برای `nulls_distinct` داد. این اجرا اثبات سازگاری PostgreSQL نیست. |
| `cd frontend && npm ci` | ۷۵ بسته نصب شد؛ گزارش npm برابر **۰ آسیب‌پذیری** بود. | بررسی dependencyهای ثبت‌شده است؛ جایگزین ممیزی کامل dependency زنجیرهٔ تأمین نیست. |
| `cd frontend && npm test` | **اجرا شد؛ ۳۴ موفق، صفر شکست، صفر skip.** | آزمون‌های موجود عمدتاً منطق/نمایش‌اند؛ E2E مرورگر روی Backend واقعی اجرا نشد. |
| `cd frontend && npm run build` | **اجرا شد؛ `tsc --noEmit` و Vite build موفق.** | موفقیت build، تطابق کامل قرارداد API یا تجربهٔ نقش‌ها را ثابت نمی‌کند. |
| اجرای `python manage.py test` روی میزبان | اجرا نشد: `ModuleNotFoundError: No module named 'django'`. | نتیجهٔ معتبر Backend در همین ارزیابی، اجرای Docker/SQLite بالاست. |
| P20: PostgreSQL 17.11، full suite | در [شواهد آزمون](./remediation/TEST-EVIDENCE.md) و [ممیزی نهایی Backend](./remediation/BACKEND-FINAL-AUDIT.md) ثبت شده: ۳۷۸ تست، صفر skip، صفر شکست. | در این نوبت مستقلاً تکرار نشد؛ تمام PostgreSQLهای آن گزارش disposable بودند، نه Production. |
| P20: migration/schema | در [شواهد PostgreSQL](./remediation/POSTGRESQL-EVIDENCE.md) ثبت شده: اجرای migrationهای 0001–0020 روی DB تازه، `makemigrations --check --dry-run` بدون تغییر. | این ارزیابی Migration جدیدی نساخت یا اجرا نکرد؛ آزمون واقعی upgrade از نسخهٔ داده‌دار/Production در دسترس نبود. |
| Git | HEAD=P20؛ commit P20 فقط ۱۰ فایل مستنداتی را تغییر داده؛ پس از نصب وابستگی‌ها و build، `git status` تغییر کد/lockfile نشان نداد. | نبود commit P19 همچنان مانع traceability فاز به فاز است. |

---

## گزارش اول — خلاصهٔ مدیریتی و آمادگی فعلی

### نتیجهٔ مدیریتی

Backend از نظر قابلیت‌های اصلی به بلوغ قابل‌توجهی رسیده است: چرخهٔ ورود/دعوت، scopeهای سازمانی، workflow مرخصی، دفترکل entitlement، audit، import/export، Bradford، job queue، اعلان و کنترل پیوست‌ها در کد وجود دارند و بخش مهمی از آن‌ها تست رگرسیون دارند. آزمون SQLite و تمام آزمون‌های Frontend در این ارزیابی اجرا و موفق شدند؛ P20 نیز شواهد آزمون کامل PostgreSQL 17.11 را ثبت کرده است.

این شواهد **مجوز انتشار Production نیستند**. P20 رفتار واقعیِ خارج از تست را هم ثبت کرده است: در PostgreSQL تازه، جدول cache پیکربندی‌شده وجود ندارد و API با production settings خطای 500 می‌دهد؛ `TRUNCATE` می‌تواند رویدادهای Audit را پاک کند؛ و درخواست/خروجی کاربر دو‌نقشی HR+manager، رکوردهایی را از مسیر manager خارج از HR assignment می‌بیند. این مشاهده دربارهٔ ترکیب نقش‌ها، snapshot تاریخی است و با تصمیم F-04 (Union) superseded شده؛ تست API جدید روی SQLite در [گزارش آزمون](./test-report.md) ثبت شده است. افزون بر آن، نقش DB عملیاتی، timer، volume/scanner، SMTP، مانیتورینگ و backup/restore عملیاتی در زیرساخت مقصد اثبات نشده‌اند.

### ارزیابی حوزه‌های اجباری

| حوزه | وضعیت فعلی | شواهد/حدود |
|---|---|---|
| Authentication و Session | **پیاده‌سازی‌شده؛ آزمون‌های واحد ثبت‌شده** | session، CSRF، Argon2، login throttling، دعوت/فعال‌سازی یک‌بارمصرف و تاریخ انقضای اشتغال پوشش دارند؛ ابطال نشست پیش‌دستانه وجود ندارد. ایمیل واقعی تأیید نشده است. |
| Authorization و RBAC | **NOT VERIFIED به‌صورت جامع** | scopeهای HR، مدیر، employee و admin برای مسیرهای مشخص تست شده‌اند؛ همهٔ routeها و ترکیب نقش‌ها ممیزی کامل ندارند. |
| Tenant isolation / HR scope | **F-04 IMPLEMENTED; SQLite API VERIFIED** | P20 ریسک باز را در snapshot خود گزارش کرد؛ تصمیم Union بعداً مصوب و با scope شرکت‌محدود در کد/تست API اعمال شد. PostgreSQL F-04 **NOT VERIFIED**؛ جزئیات در [سیاست مجوزدهی](./authorization-policy.md) و [راستی‌آزمایی امنیتی](./remediation/SECURITY-VERIFICATION.md). |
| Employee lifecycle | **بخشی تأییدشده؛ چرخهٔ کامل NOT VERIFIED** | تاریخ پایان اشتغال، activation و رد session در درخواست بعدی تست شده؛ rehire، ابطال فوری نشست و سیاست درخواست‌های باز نیازمند تصمیم‌اند. |
| Leave workflow | **بخش‌های حساس تست شده؛ همهٔ انتقال‌ها NOT VERIFIED** | ثبت HR idempotent و مستقیم approved، rollback و ledger تست شده؛ همهٔ ترکیب‌های correction/withdrawal/reopen/concurrency در این ارزیابی تأیید نشدند. |
| Balance و Entitlement | **تست‌های ledger/import ثبت‌شده** | دفترکل، رزرو/تأیید، rollback و import تکراری در PostgreSQL P20 آزموده شده؛ سیاست سال مرخصی، rollover و دادهٔ واقعی نیازمند تأیید مالک کسب‌وکار است. |
| Audit trail / Append-only | **NOT VERIFIED؛ مانع امنیتی مهم** | UPDATE/DELETE ردیفی محافظت می‌شوند؛ اجرای `TRUNCATE` روی PostgreSQL آزمایشی جدول Audit را خالی کرد. نقش/مالکیت DB مقصد مشخص یا آزموده نشده است. |
| Department و Manager hierarchy | **آزمون‌های یکپارچگی ثبت‌شده** | چرخه، رابطهٔ شرکت و تغییرات هم‌زمان در تست‌ها؛ تغییر SQL مستقیم/ابزار DBA از اعتبارسنجی سطح برنامه عبور می‌کند. |
| Site management | **Backend تست‌شده؛ Frontend ناقص** | CRUD و مرز شرکت در API و `test_organization_sites.py` پوشش دارند؛ در API client و routeهای UI فرانت‌اند صفحهٔ Site دیده نمی‌شود. |
| Import و Export | **Backend تست‌شده؛ اتصال UI ناقص** | import اتمیک و exportهای scoped/formula-safe در `test_entitlement_import.py` و `test_exports.py`؛ Frontend قرارداد/متد عمومی دانلود Export ندارد. |
| Bradford | **قابل‌اجرا اما provisional** | فرمول/دامنه/حریم خصوصی تست شده؛ سیاست تقویمی و انواع مشمول هنوز تصویب نشده، پس خروجی مبنای تصمیم نهایی HR نیست. |
| Scheduler و Background Jobs | **کد و تست موجود؛ استقرار NOT VERIFIED** | DB-backed و نمونهٔ systemd موجود؛ اجرای دستی/آزمایشگاهی جای timer فعال و پایدار را نمی‌گیرد. |
| Notification delivery | **منطق صف تست‌شده؛ provider NOT VERIFIED** | outbox و تکرار job تست شده؛ SMTP واقعی، bounce، push/SMS و رسید تحویل تأیید نشده‌اند. |
| Attachment security | **کد/test fail-closed؛ Integration NOT VERIFIED** | فقط فایل `clean` از endpoint scopeشده دانلود می‌شود؛ Production بدون command اسکنر fail-fast است. provider واقعی، volume پایدار و integration staging/Production اثبات نشده‌اند. |
| DB و Migration compatibility | **PostgreSQL test PASS در P20؛ Production NOT VERIFIED** | P20 اجرای ۲۰ migration و suite PostgreSQL 17.11 را ثبت کرده؛ upgrade داده‌دار، نسخهٔ DB مقصد و سازگاری rolling deploy اثبات نشده‌اند. |
| Backup/Restore | **drill مصنوعی موفق؛ Production NOT VERIFIED** | restore دو container با دادهٔ synthetic در P20 ثبت شده؛ offsite، زمان بازیابی واقعی، تناوب و alert وجود ندارد/تأیید نشده است. |
| HTTPS و Reverse Proxy | **تنظیمات/آزمون کد موجود؛ اتصال واقعی NOT VERIFIED** | HTTPS redirect، HSTS، cookie امن و trusted proxy در settings/test؛ TLS، شبکه و هدرهای proxy مقصد اجرا نشده‌اند. |
| Logging و Monitoring | **NOT VERIFIED / زیرساخت مشاهده نشده** | logging کنسولی؛ تجمیع، alert، retention و paging عملیاتی مستند/آزموده نیست. |
| API consistency | **قرارداد مکتوب؛ parity فرانت‌اند ناقص** | `docs/api/api-contract.md` و routeهای Backend موجودند؛ client/UI برخی endpointهای واقعی را پوشش نمی‌دهد و تست E2E یکپارچه اجرا نشده. |

**نتیجهٔ Production:** **NOT READY FOR PRODUCTION**. تست‌های سبز این نتیجه را تغییر نمی‌دهند.

---

## گزارش دوم — فهرست مشکلات و ریسک‌های باقی‌مانده

شدت‌ها بر اساس اثر و شواهد فعلی‌اند؛ برای مواردی که به policy یا privilege عملیاتی وابسته‌اند، آن وابستگی صریح ذکر شده است.

| شناسه | شدت | محل/حوزه | وضعیت و شاهد | اقدام لازم |
|---|---|---|---|---|
| SUPERSEDED BY F-04 | تاریخی: **HIGH** در snapshot P20 | `backend/leave/authorization.py`، `workflow_views.py`، `export_views.py` | P20 یک probe PostgreSQL ثبت کرده که request listing شامل direct report خارج از HR assignment بوده و CSV مقدار `AUDIT-CANARY` را داشته است. رفتار مشاهده‌شده واقعی بود، اما سیاست no-union فرضی و حل‌نشده بود؛ مالک سامانه بعداً Option A (Union) را صریحاً تأیید کرد. | تصمیم Union و ماتریس جاری در [سیاست مجوزدهی](./authorization-policy.md) ثبت شده؛ تست Session/CSRF و endpointها روی SQLite موفق است. PostgreSQL rerun **NOT VERIFIED**؛ این ریسک دیگر «تصمیم سیاستی باز» نیست. |
| R2 | **HIGH** | `backend/leave/migrations/0014_append_only_history_guards.py`، نقش‌ها و grants PostgreSQL | trigger فقط UPDATE/DELETE ردیفی را می‌گیرد. P20 با SQL مجزا `TRUNCATE TABLE public.leave_auditevent` را اجرا و count=0 مشاهده کرد. این اثبات روی DB آزمایشی/privileged است؛ امکان آن برای app role مقصد بدون مشاهدهٔ grants نامعلوم است. | app role نباید owner جدول باشد و باید مجوز `TRUNCATE`، DDL و حذف را نداشته باشد. مجوز INSERT نیز باید محدود و provenance audit محافظت شود. با credential واقعی app role، آزمایش منفی TRUNCATE/UPDATE/DELETE و بررسی مالکیت تمام append-only tables انجام شود. |
| R3 | **HIGH** | `backend/config/settings_prod.py`؛ provisioning استقرار | Cache به `DatabaseCache` و جدول `zsh_cache` اشاره می‌کند، ولی migration/deploy asset آن را ایجاد نمی‌کند. P20 در PostgreSQL تازه: جدول وجود نداشت، `/api/v1/health/` با settings تولید 500 شد؛ پس از `createcachetable zsh_cache` درخواست ۲۱ام 429 گرفت. | در release automation، پیش از ترافیک API، cache table را provision و با readiness/throttle smoke test تأیید کنید. provisioning باید idempotent و مستند باشد؛ رفتار چند worker و cache persistence نیز تست شود. |
| R4 | **HIGH** | deployment مقصد، `backend/config/settings_prod.py`، `deploy/` | production DB role، topology وب، proxy/TLS، secret injection و network boundary واقعی موجود/تأییدشده نیستند. security check با dummy env صرفاً پیکربندی Django را بررسی می‌کند. | یک staging واقعی و جدا از production بسازید؛ DB با کمترین privilege، secret manager، WSGI/server، reverse proxy، TLS و firewall را راه‌اندازی و آزمون کنید. |
| R5 | **HIGH** | `deploy/backup/`، [راهنمای backup/restore](./backup-restore-fa.md) | restore synthetic موفق است؛ backup زمان‌بندی‌شده، کپی offsite مستقل، retention/alert، RPO/RTO با حجم واقعی و restore Production **NOT VERIFIED** هستند. | backup رمز‌شده و offsite، alert تازگی/شکست و restore drill دوره‌ای روی staging واقعی؛ RPO/RTO قابل‌اندازه‌گیری را با مالک سرویس تصویب کنید. |
| R6 | **HIGH** | `deploy/systemd/`، `backend/leave/job_services.py` | job service و timer نمونه وجود دارد؛ نصب/فعال‌بودن timer/worker در میزبان مقصد ثابت نشده. نبود اجرا می‌تواند اعلان/یادآوری، escalation یا jobهای زمان‌بندی‌شده را عقب بیندازد. | timer را در staging فعال کنید؛ اجرای due job، lease/retry، heartbeat، restart host، اجرای هم‌زمان دو worker و alert شکست/عقب‌ماندگی را آزمون کنید. سیاست‌های rollover/expiration خاموش را تا تأیید HR روشن نکنید. |
| R7 | **HIGH** | `backend/leave/attachment_storage.py`، `attachment_security.py`، `attachment_scanning.py`، `backend/config/settings_prod.py` | دانلود به `clean` محدود و config scanner در Production اجباری است؛ volume پایدار/مجوز OS و provider واقعی هنوز تأیید نشده‌اند. | volume خارج repository با backup همگام، owner/permission محدود، محدودیت ظرفیت، retention و scanner واقعی طبق adapter قراردادشده پیکربندی و در staging با فایل بی‌خطر و نمونهٔ تست آلودهٔ سازمانی بررسی شود. |
| R8 | **HIGH** | `backend/config/settings_base.py`، `backend/config/settings_prod.py`، عملیات | P20 نبود log aggregation، alert، retention، backup freshness alert، scheduler alert و incident paging را ثبت می‌کند. | لاگ ساختاریافته با redaction، شناسهٔ correlation، مانیتور خطای API/DB، job lag، queue، backup، storage و certificate expiry را به سامانهٔ عملیاتی وصل کنید؛ مالک/آستانه/مسیر escalation تعیین شود. |
| R9 | **MEDIUM** | `backend/leave/authorization.py`، `authentication_classes.py`، account lifecycle | session هشت‌ساعته است و اشتغال در request بعدی بررسی می‌شود؛ revoke پیش‌دستانه/session version وجود ندارد. رفتار پس از termination به فاصله تا درخواست بعدی وابسته است. | پیش از انتشار، risk acceptance برای session termination بگیرید؛ حداکثر زمان revoke را تعیین و logout/offboard/session موجود را در تست E2E تأیید کنید. |
| R10 | **MEDIUM** | `backend/leave/employment_services.py`، `invitation_services.py` و policyهای HR | end-date و activation آزمون دارند؛ rehire با دوره‌های مجزا، سابقهٔ کامل lifecycle و تکلیف درخواست‌های pending هنگام offboarding تصمیم/تست کامل ندارند. | سیاست HR برای rehire، future end-date، غیرفعال‌سازی، درخواست‌های باز و نگهداری تاریخچه را تصویب و سناریوهای مرزی را تست کنید. |
| R11 | **MEDIUM** | `backend/leave/bradford_services.py`، `zsh_core/reports.py`، `docs/bradford-policy-fa.md` | گزارش `S² × D` و scope تست شده اما `provisional` است؛ روز تقویمی/کاری، تعطیلات، پاره‌وقت و انواع واجد شرایط تعیین نهایی نشده‌اند. | مالک HR/حقوقی policy را امضا کند؛ فرمول و مثال مرجع را تأیید کند و خروجی تا آن زمان فقط با برچسب provisional نمایش داده شود. |
| R12 | **MEDIUM** | `backend/leave/audit_services.py`، `models.py`، سرویس‌های سازمان/ledger | مسیرهای API ثبت Audit دارند، اما ORM/script/SQL مستقیم می‌تواند service audit را دور بزند؛ triggerهای row-level نیز INSERT را احراز نمی‌کنند. hierarchy نیز در SQL مستقیم از validation سطح برنامه عبور می‌کند. | دسترسی مستقیم را محدود، هر عملیات داده‌ای مدیریتی را ثبت و reconcile کنید؛ runbook تغییرات DBA، diagnostic دوره‌ای hierarchy و آزمون negative با app role تدوین شود. |
| R13 | **MEDIUM** | `backend/leave/notification_services.py`، `0020_notificationdelivery`، SMTP | outbox و retry در تست هستند؛ تأیید SMTP provider، bounce و تحویل نهایی وجود ندارد؛ وضعیت `sent` الزاماً `delivered` نیست. | staging را به SMTP آزمایشی وصل کنید، صف/خطا/retry/provider response را رصد کنید و معنای وضعیت‌ها و مسیر رسیدگی به bounce را روشن کنید. |
| R14 | **MEDIUM** | `backend/leave/migrations/0001..0020`، PostgreSQL مقصد | fresh migration و test PostgreSQL 17.11 در P20 موفق‌اند؛ upgrade از دادهٔ موجود، نسخهٔ واقعی DB، lock/downtime و backward-compatible rollout در مقصد تست نشده. SQLite نیز هشدار `W047` دارد. | روی snapshot پاک‌سازی‌شدهٔ staging از schema واقعی upgrade/rollback rehearsed انجام دهید؛ زمان قفل/حجم را بسنجید. SQLite را جایگزین آزمون PostgreSQL نکنید. |
| R15 | **MEDIUM** | `frontend/src/api/contract.ts`، `frontend/src/api/http.ts`، `frontend/src/App.tsx` | API واقعی سایت، role assignments، invitation/revoke/suspend، و export را دارد؛ در قرارداد/HTTP client فعلی متد متناظر یا UI کامل مشاهده نشد. تفاوت Frontend و Backend مانع انجام بخشی از عملیات روزمره است، نه مجوز امنیتی Backend. | در مرحلهٔ Frontend، از قرارداد واقعی `docs/api/api-contract.md` شروع و قابلیت‌ها را موردی با schema/status/pagination/auth آزمون کنید؛ از mock/demo به‌عنوان شواهد API استفاده نکنید. |
| R16 | **MEDIUM** | `frontend/src/api/http.ts`، `client_policy`، تنظیمات hourly | Frontend منطق ساعت‌مرخصی دارد؛ محدودیت‌های جاری می‌گویند `hourlyLeaveEnabled=false` در client policy است. قابلیت نمایشی به معنی فعال/پشتیبانی‌شده در Backend نیست. | تا فعال‌سازی و آزمون policy واقعی، فرم ساعتی را در حالت غیرفعال نگه دارید؛ قرارداد request/estimate/balance و Bradford برای ساعت را با Backend تثبیت کنید. |
| R17 | **MEDIUM** | کل API و UI؛ `docs/api/api-contract.md` | تست Backend روی API و تست Frontend روی منطق client جدا هستند؛ E2E مرورگر با session/CSRF، roleهای واقعی، دانلود export، upload attachment و scheduler اجرا/ثبت نشده. | تست E2E را در staging با حساب‌های synthetic برای HR، مدیر، employee، admin و دوشرکتی اجرا کنید؛ خروجی/trace را ثبت کنید. |
| R18 | **LOW** | `docs/remediation/PROGRESS.md`، `PRODUCTION-GATE.md`، `KNOWN-LIMITATIONS.md` و Git history | بخش‌های تاریخیِ قدیمی در کنار نتایج جاری باقی‌اند؛ تاریخچهٔ موجود P19 مجزا ندارد. احتمال برداشت نادرست از status و فقدان traceability وجود دارد. | در release record، commit/tag و ماتریس requirement→code→test تثبیت شود و snapshot قدیمی به‌وضوح با پیوند/هشدار superseded نگه داشته شود. |
| R19 | **MEDIUM** | `frontend/src/api/http.ts`، `createRequest`، `recordAbsence` و `actRequest` | هر invocation یک `Idempotency-Key` جدید می‌سازد. اگر پاسخ mutation گم شود و کاربر همان عملیات را دوباره submit کند، retry ممکن است کلید تازه بگیرد و به‌عنوان intent جدید دیده شود؛ retry مبهم در E2E بررسی نشده است. | کلید را در lifecycle همان intent نگه دارید، پاسخ مبهم را با همان کلید retry کنید و برای ایجاد درخواست/غیبت/transition تست شبکهٔ قطع‌شونده اضافه کنید. |

### وضعیت قابلیت‌های P01–P19 و ممیزی P20

- مواردی که در درخت فعلی و شواهد آزمون مشخص دیده می‌شوند: بخش‌هایی از authentication/invitation، authorization و scope، lifecycle تاریخ‌محور، workflow و idempotency، entitlement ledger/import، hierarchy/Site، Bradford، export، notification outbox، attachment ACL، audit service و scheduler. شواهد آزمون/محدودیت هر مورد در [ممیزی نهایی Backend](./remediation/BACKEND-FINAL-AUDIT.md)، [ماتریس audit](./remediation/AUDIT-COVERAGE-MATRIX.md) و جدول حوزه‌های بالا درج شده است.
- این نتیجه به معنای «تمام acceptance criteria فازهای P01–P19 تکمیل‌اند» نیست؛ mapping رسمیِ هر P به معیار و تست در مخزن موجود نیست و P19 نیز commit مستقل قابل‌شناسایی ندارد.
- P20 یک audit/documentation reconciliation است، نه تغییر کد backend. یافته‌های باز R1–R8 مهم‌ترین نتایج عملیاتی/امنیتی آن‌اند.

---

## گزارش سوم — چک‌لیست Staging و تست عملیاتی

**وضعیت فعلی Staging:** محیط Staging واقعی در شواهد بررسی‌شده وجود ندارد؛ تمام موارد عملیاتی زیر **NOT VERIFIED** هستند تا در محیط مقصد اجرا و مدرک آن ثبت شود. چک‌باکس‌ها برنامهٔ اجرا هستند، نه PASS.

### A. آماده‌سازی محیط

- [ ] محیط، DNS، حساب‌ها، secretها، DB، storage و SMTP از Production جدا باشند؛ دادهٔ واقعی HR کپی نشود مگر با تصویب/ناشناس‌سازی رسمی.
- [ ] نسخهٔ PostgreSQL مقصد را ثبت کنید؛ با role واقعی app و role مجزای backup وصل شوید؛ app مالک schema/tableهای Audit نباشد.
- [ ] secrets را از secret manager وارد کنید: `ZSH_SECRET_KEY`، `ZSH_DB_*`، `ZSH_ALLOWED_HOSTS`، `ZSH_CSRF_TRUSTED_ORIGINS`، `ZSH_TRUSTED_PROXY_IPS`، `ZSH_ATTACHMENT_ROOT`، `ZSH_ATTACHMENT_SCANNER_COMMAND` و email settings.
- [ ] app فقط از شبکهٔ داخلی/Unix socket قابل‌دسترسی باشد؛ TLS، certificate chain، Host، HSTS، redirect و proxy trust با دامنه و peer واقعی آزموده شوند.
- [ ] پیش از ترافیک، `createcachetable zsh_cache` را طبق runbook اجرا و health، throttling، رفتار چند process و restart را تأیید کنید.
- [ ] `check --deploy --tag security` و `makemigrations --check --dry-run` را با config staging واقعی اجرا و خروجی/هش نسخه را نگه دارید؛ بررسی system check به‌تنهایی جای آزمون درخواست واقعی نیست.

### B. داده، مهاجرت و مجوز

- [ ] روی snapshot غیرحساس staging، upgrade را با همان مسیر deployment اجرا کنید؛ تعداد migration، lock/downtime، شمار رکوردها و سازگاری app قبلی/جدید ثبت شود.
- [ ] از app role تلاش کنترل‌شده برای `TRUNCATE`، `DELETE`، `UPDATE` و INSERT مستقیم روی Audit/history کنید؛ انتظار deny و audit provenance روشن است. این آزمون را فقط روی DB آزمایشی انجام دهید.
- [ ] اقدام R1 در P20 (تعیین سیاست چندنقشی و آزمون مسیرها) با تصمیم F-04 و تست‌های SQLite انجام شده است؛ برای اثبات PostgreSQL، تست‌های F-04 را با migration جدید و credentialهای test در PostgreSQL مستقل اجرا کنید (**NOT VERIFIED**).
- [ ] ماتریس RBAC هر endpoint را برای employee، manager، HR در scope، HR خارج scope، executive، admin و superuser پر کنید؛ پاسخ 401/403/404 و عدم side effect را بررسی کنید.
- [ ] company/site/department/manager تغییر، بستن Site، reassignment کارمند و تلاش ساخت چرخهٔ مدیر را بررسی کنید.
- [ ] invitation، activate، انقضا، revoke، suspend، password، end-date گذشته/امروز/آینده، نشست موجود و rehire را اجرا کنید؛ دادهٔ token خام نباید در DB/log/audit باشد.

### C. عملیات کسب‌وکار و data integrity

- [ ] workflow: submit، approve، reject، cancel، withdraw، approve/reject withdrawal، amend، correction، idempotent replay و conflict را با کاربرهای واقعی Staging اجرا کنید.
- [ ] ledger: balance پیش/پس، رزرو، approval، cancellation/withdrawal، adjustment، import معتبر/نامعتبر/تکراری و rollback را با مقادیر کوچک کنترل کنید؛ reconciliation قبل/بعد باید ثبت شود.
- [ ] rollover/expiration را تا تصویب policy خاموش نگه دارید؛ در صورت تصویب، اجرای job را فقط روی data fixture و سپس روی مجموعهٔ آزمایشی مستقل بررسی کنید.
- [ ] Bradford را با دادهٔ مرجع HR و سیاست تصویب‌شده تطبیق دهید؛ تا امضا، خروجی را provisional و غیرقابل‌استفاده برای تصمیم قطعی علامت بزنید.
- [ ] پیوست را با انواع/اندازه‌های مجاز و نامعتبر، دانلود employee/HR مجاز و سایر نقش‌ها، `pending_scan/rejected/scanner_unavailable/scan_failed`، timeout، retry و symlink بررسی کنید. نمونهٔ scanner آلوده را فقط از fixture تست سازمانی بگیرید؛ هرگز از بدافزار واقعی استفاده نکنید.
- [ ] اعلان‌های invitation، approved/rejected/reminder را از outbox تا SMTP آزمایشی دنبال کنید؛ خطای provider، retry، duplicate prevention و bounce را ثبت کنید.
- [ ] Exportهای CSV/XLSX را برای scope، داده محرمانه، formula injection، row/date limit، encoding فارسی و عدم persistence فایل روی server بررسی کنید.

### D. Job، observability، backup و smoke

- [ ] systemd timer/service واقعی Staging را نصب/فعال کنید؛ heartbeat، due job، دو worker هم‌زمان، retry، lock/lease، restart و backfill را ثبت کنید.
- [ ] log aggregation و dashboard/alert برای 5xx، latency، DB connection، throttle، job lag/failure، notification queue، scanner، backup freshness، disk و TLS expiry را آزمایش کنید؛ secrets/PII نباید log شوند.
- [ ] با `deploy/backup/` از DB و attachment هم‌زمان backup بگیرید؛ checksum را بررسی، سپس به DB/storage خالی staging دیگری restore کنید؛ inventory، migration، FK، ledger، Audit و فایل‌ها را reconcile کنید.
- [ ] زمان backup/restore را اندازه بگیرید و RPO/RTO را با مالک سرویس تصویب کنید؛ drill مصنوعی کوچک به‌تنهایی کفایت نمی‌کند.
- [ ] با TLS واقعی Staging، `health` و `ready`، login/logout، CSRF، session timeout، API بنیادی، upload/download و Frontend مرورگر را smoke کنید.
- [ ] suite Backend را روی PostgreSQL staging با صفر skip و Frontend را با `npm test`/`npm run build` اجرا کنید؛ سپس E2E را اجرا کنید. نتیجهٔ SQLite را جایگزین PostgreSQL نکنید.
- [ ] خطاها، تصمیم policy، خروجی دستورات، timestamp، commit hash، نسخهٔ DB، test report و امضای مالک محصول/امنیت/عملیات را ثبت کنید.

**شرط عبور Staging:** R1 (سیاست scope)، R2 (privilegeهای Audit)، R3 (cache)، مسیر HTTPS، scheduler، storage/scanner، backup/restore و هشدارهای پایه باید حل یا با risk acceptance مکتوب محدود شده باشند. محیطی که این شروط را ندارد فقط sandbox توسعه با دادهٔ مصنوعی است، نه Staging آمادهٔ پذیرش.

---

## گزارش چهارم — چک‌لیست Production Deployment و Rollback

### قبل از انتشار — release gate

- [ ] همهٔ gateهای security/scope، append-only و cache بسته شده و evidence با commit جاری پیوند خورده باشد.
- [ ] مالک HR سیاست workflow، entitlement/year/rollover، Bradford، lifecycle، notification، retention و دسترسی دو‌نقشی را تصویب کرده باشد.
- [ ] image/artifact immutable با commit، SBOM/dependency scan، build Frontend و test report مشخص ساخته شود؛ image ساخته‌نشده/دستی در Production deploy نشود.
- [ ] PostgreSQL نسخه‌دار/پشتیبانی‌شده، TLS DB در صورت نیاز، backup user جدا، app role کم‌اختیار و grants Audit مستند باشند.
- [ ] secretها در secret manager؛ کلیدها، SMTP، domain/CSRF، trusted proxy CIDR، attachment root و scanner command با مقادیر واقعی و جدا از Staging تنظیم شوند.
- [ ] reverse proxy، TLS، Host، HSTS، cookie secure، CSRF، network ACL و جلوگیری از دسترسی مستقیم به upstream از شبکهٔ عمومی در rehearsal تأیید شده باشد.
- [ ] `zsh_cache` قبل از عبور ترافیک provision شود؛ deploy در صورت فقدان table متوقف شود.
- [ ] backup encrypted/offsite با retention و alert فعال باشد؛ restore drill اخیر، زمان بازیابی و RPO/RTO پذیرفته‌شده موجود باشد.
- [ ] systemd timer/worker یا scheduler جایگزین، job ownership، timezone، restart و alert آزمایش شده باشد؛ jobهای مخرب/مالی تا تصویب policy غیرفعال بمانند.
- [ ] private attachment volume، حداقل مجوز، scanner، ظرفیت، retention و backup/restore آن آماده باشد.
- [ ] مانیتورینگ و paging، runbook رخداد، مالک on-call، کانال پاسخ و log retention/redaction عملیاتی باشند.
- [ ] Frontend با endpointهای Backend سازگار باشد؛ نقش‌ها، form validation، دانلود فایل، session/CSRF و صفحه‌های خطا در E2E staging آزموده شده باشند.

### ترتیب انتشار پیشنهادی

1. freeze تغییرات، ثبت commit/artifact/config و تأیید release gate؛ اعلام maintenance/rollback owner.
2. گرفتن backup سازگار DB و media؛ ثبت checksum/inventory و تأیید قابلیت decrypt/restore.
3. drain ترافیک و uploader/importerها در صورت نیاز؛ متوقف‌کردن timer/jobهای در حال اجرا و ثبت heartbeat/queue.
4. اعمال تغییر DB فقط پس از rehearsal؛ در این ارزیابی Migration تازه‌ای ایجاد نشده است. هر Migration انتشار آتی باید جداگانه بررسی و plan شود.
5. اجرای مرحلهٔ provision cache (`createcachetable zsh_cache`) و تأیید privilegeهای app role.
6. انتشار artifact نسخه‌دار Backend/Frontend، فعال‌سازی سرویس‌ها و readiness؛ ابتدا smoke محدود با حساب‌های synthetic.
7. اجرای workflowهای مجاز، permission smoke، notification/job و attachment tests؛ مانیتور کردن 4xx/5xx، latency، queue، DB و storage.
8. بازگشایی ترافیک تدریجی؛ نگه‌داشتن نسخهٔ قبلی و backup مطابق retention؛ ثبت زمان/متریک/تصمیم.
9. پس از پنجرهٔ مشاهده، timer/jobهای تصویب‌شده را فعال و اجرای اولین slot را با کنترل duplicate و ledger reconcile تأیید کنید.

### Rollback و بازیابی

- [ ] معیار rollback از قبل تعیین شود: افزایش 5xx، نشت scope، خطای migration/DB، reconciliation ledger ناموفق، تکرار job/اعلان، scanner/storage failure یا readiness پایداراً ناسالم.
- [ ] ابتدا ترافیک mutation را متوقف و job/uploader/importer را pause کنید؛ queue، logs، correlation IDs و snapshot وضعیت را برای forensic حفظ کنید.
- [ ] اگر فقط application regression است و schema backward-compatible بودن آن **در rehearsal ثابت شده**، artifact قبلی را deploy و health/ready، login، permissions و mutation smoke را اجرا کنید.
- [ ] Migration را به‌صورت خودکار reverse نکنید. rollback schema فقط با plan تاییدشده و آزموده‌شده انجام شود؛ اگر schema ناسازگار/داده آسیب‌دیده است، روی محیط جدا restore هماهنگ DB+media از snapshot سالم انجام و سپس cutover کنترل‌شده کنید.
- [ ] restore واقعی به محیط خالی/جدا ابتدا تأیید شود؛ runbook موجود برای staging target خالی طراحی شده و به‌تنهایی دستور بازگردانی Production نیست.
- [ ] پس از rollback/restore، migration state، Foreign Key، ledger/balance، Audit/history، فایل‌ها/hash، job effects، notification queue و sessionها را بررسی کنید.
- [ ] jobها را پس از تطبیق idempotency فعال کنید؛ از اجرای مجدد rollover/expiration یا ارسال duplicate خودداری کنید.
- [ ] نتیجه، نقطهٔ بازیابی، دادهٔ از دست‌رفته/تکرارشده، RPO/RTO مشاهده‌شده و علت rollback ثبت و به incident owner اعلام شود.

**وضعیت فعلی:** هیچ rollback Production یا restore روی زیرساخت Production اجرا/تأیید نشده است.

---

## گزارش پنجم — برنامهٔ تکمیل Frontend بر پایهٔ API واقعی

Backend routeها و قراردادشان در [`backend/leave/urls.py`](../backend/leave/urls.py) و [`docs/api/api-contract.md`](./api/api-contract.md) است. Frontend فعلی در [`frontend/src/api/contract.ts`](../frontend/src/api/contract.ts)، [`frontend/src/api/http.ts`](../frontend/src/api/http.ts) و [`frontend/src/App.tsx`](../frontend/src/App.tsx) از API client مشترک استفاده می‌کند. فهرست زیر برنامه است و در این ارزیابی پیاده‌سازی نشده است.

### مرحلهٔ ۰ — تثبیت parity قرارداد و test harness

- تطبیق method/path/header/query/body/status/error shape و pagination هر متد client با endpoint واقعی؛ قرارداد نوعی را از route behavior و test fixtures تولید/تأیید کنید.
- تست contract برای login/CSRF، idempotency، `401/403/404/409/422/429`، فرم multipart و پاسخ CSV/XLSX اضافه کنید.
- test environment را با Backend واقعی و fixtureهای synthetic راه‌اندازی کنید؛ demo mode فقط در توسعه بماند و هرگز شاهد پذیرش API تلقی نشود.
- مبنای Done: هیچ endpoint فعال UI بدون contract test؛ `npm test`, `npm run build` و E2E Staging سبز با گزارش ثبت‌شده.

### مرحلهٔ ۱ — Admin، RBAC و چرخهٔ حساب

- به `Api` و HTTP adapter اضافه شود: `/admin/role-assignments/` و revoke؛ `/employees/{id}/invitation/`، `/invitation/revoke`، `/suspend`؛ `/auth/activate` برای صفحهٔ فعال‌سازی دعوت.
- صفحهٔ مدیریت grant/revoke نقش با توضیح scopeهای company/site/department/employee، تأیید عملیات، نمایش actor/status و جلوگیری از self-grant مطابق خطای Backend.
- در فرم Employee، ارسال/ارسال مجدد/لغو invitation، suspend و نمایش وضعیت دعوت/اشتغال با پیام خطای server.
- تست role matrix برای global admin، HR scoped و actor خارج scope؛ route guard صرفاً UX است و نباید مرجع authorization معرفی شود.

### مرحلهٔ ۲ — سازمان و Site

- صفحهٔ Site برای فهرست/ایجاد/ویرایش/archive و حذف فقط در صورت مجاز بودن؛ ownership شرکت immutable، هشدار archive و نمایش employeeهای تاریخی را رعایت کند.
- فرم Employee امکان انتخاب Site فعال همان Company را بدهد؛ هنگام جابه‌جایی شرکت، Site ناسازگار پاک/بازانتخاب شود و 409/422 سرور نمایش داده شود.
- صفحهٔ Company/Department فعلی با hierarchy، manager، parent، status و خطای cycle/server هم‌راستا شود؛ هر قابلیت write باید با audit reason در صورت الزام API کار کند.
- endpoint مبنا: `/sites/`, `/sites/{id}/`, `/companies/`, `/departments/`, `/employees/{id}/`.

### مرحلهٔ ۳ — Export و Import

- Import entitlement فعلی را با preview/commit، خطای هر ردیف، all-or-nothing و تأیید تعداد/سال/مجموع با Backend واقعی آزمون کنید.
- Download API برای `/exports/{dataset}.{csv|xlsx}` اضافه شود؛ helper باید پاسخ `Blob`/header نام فایل را مدیریت کند و `req()` فعلی که JSON فرض می‌کند برای دانلود به‌کار نرود.
- datasetهای مجاز را از contract واقعی انتخاب کنید؛ query filters، pagination/limit، confidential redaction و نقش را نمایش دهید. خروجی نباید در UI برای دادهٔ خارج scope قابل انتخاب باشد، ولی Backend همچنان مرجع دسترسی است.
- CSV فارسی، XLSX، formula-safe output، row/date cap، نام فایل و حالت شبکه/401 را E2E بررسی کنید.

### مرحلهٔ ۴ — صفحه‌های عملیاتی و observability

- صفحهٔ Jobs را به `/admin/jobs/` و وضعیت واقعی readiness/heartbeat متصل کنید؛ `health()` فعلی از `/ready/` استفاده می‌کند و `lastJob: null` می‌سازد، پس نباید last-job جعلی نمایش دهد.
- وضعیت صف اعلان و failure را با معنی درست `queued/sent/delivered/failed/unavailable` نشان دهید؛ `sent` را delivery قطعی ترجمه نکنید.
- صفحهٔ Audit، شناسهٔ correlation و جزئیات را فقط مطابق خروجی مجاز هر role نشان دهد؛ متن دلیل/قبل‌و‌بعد HR را افشا نکند.
- loading/empty/error/retry و keyboard/screen-reader accessibility برای تمام صفحات عملیاتی اضافه و در نقش‌های مجاز/غیرمجاز تست شود.

### مرحلهٔ ۵ — Workflow، سیاست و پیام‌های business

- انتقال‌های مجاز workflow را از server state بسازید؛ دکمهٔ optimistic نباید وضعیت نهایی را قبل از پاسخ/تأیید server قطعی کند.
- برای idempotency، در retry همان mutation همان کلید تا دریافت نتیجه حفظ شود؛ ساخت UUID جدید در retry مبهم می‌تواند duplicate intent بسازد. رفتار فعلی در `http.ts` برای هر invocation کلید تازه می‌سازد و باید در UX retry با semantics Backend تطبیق داده شود.
- Bradford را با برچسب provisional و هشدار policy نمایش دهید تا تصویب رسمی؛ آستانه و روزکاری/تعطیل را در UI hardcode نکنید.
- مرخصی ساعتی تا زمانی که Backend `client_policy` آن را فعال و endpoint/محاسبه/مانده‌اش تأیید نکرده، غیرقابل‌ثبت نمایش داده شود؛ محاسبهٔ محلی تنها نمایش کمکی است.
- تنظیمات/calendar policies و holiday administration را فقط با مجوز system configuration و audit reason به UI وصل کنید.

### مرحلهٔ ۶ — پذیرش Frontend

- E2E در مرورگر واقعی با session cookie، CSRF، HTTP/HTTPS proxy، نقش‌های مختلف و API جاری اجرا شود.
- سناریوهای انتخابی: login/logout و انقضا؛ درخواست و manager decision؛ HR absence؛ entitlement import؛ Site/employee؛ export؛ attachment؛ Bradford provisional؛ job failure و notification unavailable.
- تست visual/accessibility، locale و تاریخ شمسی/میلادی، شبکهٔ کند/قطع‌شده، pagination و responsive layout اجرا شود.
- release frontend فقط وقتی API compatibility، security review، E2E و build artifact همان commit همگی ثبت شده‌اند پذیرفته شود.

---

## تصمیم نهایی

# NOT READY FOR STAGING

**دلایل فنی در snapshot P20:** (۱) اثبات grants append-only هنوز باز است؛ (۲) DB تازه بدون provisioning جدول `zsh_cache` در مسیر درخواست production به خطای 500 می‌رسید؛ (۳) محیط Staging واقعی با proxy/TLS، DB role، timer، attachment volume/scanner، SMTP، monitoring و backup/restore در شواهد موجود نبود. scope چندنقشی از این فهرست با تصمیم F-04 حل شده، اما تست PostgreSQL آن **NOT VERIFIED** است. آزمون SQLite و Frontend به‌تنهایی موانع عملیاتی را نمی‌بندند. شواهد جاری و تاریخی در [ممیزی نهایی Backend](./remediation/BACKEND-FINAL-AUDIT.md)، [امنیت](./remediation/SECURITY-VERIFICATION.md)، [PostgreSQL](./remediation/POSTGRESQL-EVIDENCE.md)، [عملیات Production](./remediation/PRODUCTION-OPERATIONS.md) و [سیاست مجوزدهی](./authorization-policy.md) آمده است.

**Production نیز قطعاً آماده نیست.** برای بازبینی مجدد، R1–R8 باید بسته یا با تصمیم risk acceptance رسمی محدود شوند، چک‌لیست Staging اجرا و ثبت شود و سپس آزمون‌های PostgreSQL/Front-end و restore روی محیطی هم‌پیکربندی با مقصد با موفقیت تکرار شوند.
