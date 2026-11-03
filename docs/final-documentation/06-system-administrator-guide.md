# راهنمای مدیر سامانه و عملیات

> راهنمای جاری LeaveType در [راهنمای مدیریت انواع مرخصی](../leave-types-guide-fa.md) است. هر مستند قدیمی که نوشتن Global LeaveType را به HR نسبت دهد برای این قابلیت **Superseded** است.

نقش `admin`، پیکربندی فنی و ساختار را مدیریت می‌کند اما در `scoped_leave_requests` به درخواست فردی دسترسی ندارد. `is_superuser` نیز admin می‌شود؛ دسترسی داده مرخصی با مجوز HR برابر نیست.

## پیکربندی و نصب محلی

متغیرهای خوانده‌شده در settings:

| متغیر | کاربرد |
|---|---|
| `ZSH_ENV` | در `config/settings.py` برای انتخاب module dev/test/prod استفاده می‌شود؛ پیش‌فرض `development` است |
| `ZSH_DEBUG` | settings پایه DEBUG؛ `settings_dev` آن را روی True بازنویسی می‌کند |
| `ZSH_SECRET_KEY` | کلید Django؛ production لازم و حداقل ۵۰ نویسه |
| `ZSH_ALLOWED_HOSTS` | فهرست comma-separated؛ production غیرخالی لازم |
| `ZSH_DB_ENGINE`, `ZSH_DB_NAME`, `ZSH_DB_USER`, `ZSH_DB_PASSWORD`, `ZSH_DB_HOST`, `ZSH_DB_PORT` | اتصال settings_base |
| `ZSH_CSRF_TRUSTED_ORIGINS` | فهرست originهای trusted |
| `ZSH_TRUSTED_PROXY_IPS` | CIDRهای peer مستقیم proxyهای تحت کنترل؛ فقط آن peerها برای `X-Forwarded-For` و `X-Forwarded-Proto` trusted هستند؛ پیش‌فرض خالی |
| `ZSH_USE_SQLITE` | override خاص `settings_dev.py`; `1` SQLite |

نمونه `.env.example` شامل مقدارهای صرفاً توسعه است و Django dotenv loader در مخزن دیده نشد. `settings_prod.py` PostgreSQL را اجباری می‌کند و password DB می‌خواهد. کلیدها را در مستند، Git یا log قرار ندهید.

`config/settings.py` یک wrapper واقعی است که `ZSH_ENV` را به `settings_dev`, `settings_test` یا `settings_prod` نگاشت می‌کند؛ مقدار ناشناخته نیز به development می‌افتد. برای فرمان‌های عملیاتی settings را صریح تعیین کنید تا تصادفاً development انتخاب نشود:

```bash
cd backend
ZSH_USE_SQLITE=1 DJANGO_SETTINGS_MODULE=config.settings_dev python3 manage.py migrate
DJANGO_SETTINGS_MODULE=config.settings_test python3 manage.py test
```

برای production می‌توان `ZSH_ENV=production` را به wrapper داد یا `DJANGO_SETTINGS_MODULE=config.settings_prod` را مستقیم تعیین کرد. `.env.example` خودکار بارگذاری نمی‌شود. خود deployment/runner و secret management باید جداگانه فراهم و در محیط هدف آزموده شوند. هیچ `createsuperadmin` یا `seed_demo` command در repository یافت نشد.

## مدیریت کاربر، نقش و سازمان

- کاربران Django و EmployeeProfile به‌صورت جداگانه مدیریت می‌شوند. UI مدیریت employee هنگام ایجاد حساب، نام کاربری تولید و password unusable می‌گذارد؛ provisioning/login امن برای آن باید سازمانی تعریف شود. در `authorization_context`، `end_date` به‌تنهایی نقش employee را نمی‌بندد؛ خاتمه دسترسی باید با غیرفعال‌سازی حساب یا `is_employee=False` نیز همراه باشد.
- نقش‌ها در `RoleAssignment` هستند: admin-global؛ HR شرکت/سایت/واحد/کارمند؛ exec شرکت/سایت/واحد. manager از رابطه مستقیم EmployeeProfile به دست می‌آید. RoleAssignment می‌تواند `expiresAt` آینده و timezone-aware داشته باشد؛ غیرفعال/منقضی هیچ scope مؤثری ندارد.
- endpoint admin نقش‌ها `/api/v1/admin/role-assignments/` و جزئیات DELETE دارد؛ UI/متد frontend وجود ندارد. اعطای نقش به خود ممنوع، grant/revoke/انقضا ممیزی و لغو آخرین admin مؤثر برای actor غیر-superuser ممنوع است. سیاست ترکیب چندنقشی در [سیاست مجوزدهی](../authorization-policy.md) آمده است.
- company/department از UI سازمانی مدیریت‌پذیر است؛ سایت‌مدیریت UI جدا ندارد. رکوردها را فقط در سازمان/داده واقعی پس از اعتبارسنجی مرجع بسازید.

## امنیت، نشست و تنظیمات کسب‌وکار

`SessionAuthentication`, `CsrfViewMiddleware`, `HttpOnly`, `SameSite=Lax`, cookie secure در non-debug، throttleها، گذرواژه minimum 12 و Argon2 در settings هستند. پاسخ throttle برابر 429 و دارای `Retry-After` است. در production، شمارنده‌ها در جدول مشترک `zsh_cache` روی PostgreSQL قرار می‌گیرند؛ جدول را یک‌بار با `DJANGO_SETTINGS_MODULE=config.settings_prod python manage.py createcachetable zsh_cache` ایجاد کنید و اتصال یکسان تمام workerها را بررسی کنید. حافظه محلی process برای rate limiting چند-worker کافی نیست. backend واقعی deployment در این repository قابل تأیید نیست، پس تا بررسی آن ادعای محدودسازی سراسری نکنید.

هدرهای `X-Forwarded-For` و `X-Forwarded-Proto` فقط از آدرس‌های داخل `ZSH_TRUSTED_PROXY_IPS` پذیرفته می‌شوند. این CIDRها را به peer مستقیم proxyهای تحت کنترل محدود کنید؛ proxy باید header ورودی کاربر را بازنویسی کند و اتصال مستقیم عمومی به backend باید در شبکه بسته باشد. عملیات command/job که مستقیماً سرویس‌های Django را اجرا می‌کند از throttle HTTP عبور نمی‌کند. نرخ‌های حساس، scopeهای موجود و endpointهای password reset/export که هنوز وجود ندارند در [security.md](../security.md) ثبت شده‌اند.

`settings_prod.py` HTTPS redirect/HSTS و `SECURE_PROXY_SSL_HEADER` را فعال می‌کند؛ middleware فقط هدر دقیق `X-Forwarded-Proto: https` را از IP داخل `ZSH_TRUSTED_PROXY_IPS` می‌پذیرد و از دیگر peerها حذف می‌کند. برای TLS termination روی proxy، CIDR peer واقعی را تنظیم و overwrite هدر را در proxy تضمین کنید. با CIDR خالی، هیچ forwarded header مورد اعتماد نیست و upstream HTTP به HTTPS redirect می‌شود؛ در TLS مستقیم تا WSGI، scheme امن از سرور WSGI می‌آید. production فقط originهای CSRF دقیق HTTPS می‌پذیرد و `ALLOWED_HOSTS=*` رد می‌شود.

توپولوژی وب production، TLS proxy، TLS خود سرویس، تنظیمات TLS cipher، CSP، rate-limit لبه و WAF در مخزن پیکربندی نشده‌اند. [راهنمای استقرار](../deployment.md) الگوی بی‌طرف از محصول، smoke test HTTPS/HTTP/health و تصمیم‌های لازم operator را توضیح می‌دهد. پیش از انتشار، دسترسی مستقیم upstream را در firewall/network ببندید و smoke test را در staging با گواهی و دامنه واقعی اجرا کنید.

تنظیمات schema-backed در «تنظیمات سامانه» یا GET/POST `/api/v1/admin/settings/` موجودند؛ تغییر با دلیل audit می‌شود. هر تنظیم مقدار مجاز خودش را در `zsh_core/settings_schema.py` دارد. محرمانگی `privacy.*`, حداقل گروه، تنظیمات سیاست/گردش کار را خارج از تصمیم مالک محصول تغییر ندهید.

## مهاجرت، DB، فایل و بازیابی

- مهاجرت‌های `backend/leave/migrations/0001_initial.py` به بعد منبع schema هستند؛ migration `0019_attachment_security` metadata امنیت/retention را اضافه و فایل‌های تاریخی را با تطبیق SHA-256 از `MEDIA_ROOT` به `ZSH_ATTACHMENT_ROOT` کپی می‌کند.
- پیش از migration، backup و نسخه DB را ثبت کنید؛ در pre-prod همان migrationها را روی PostgreSQL واقعی/نسخه سازگار آزمایش کنید.
- SQLite برای آزمون سبک است؛ قیود `nulls_distinct` و trigger/locking PostgreSQL را کامل تضمین نمی‌کند.
- پیوست‌ها در `ZSH_ATTACHMENT_ROOT` خصوصی (مطلق، خارج از repository، directory `0700` و file `0600`) ذخیره می‌شوند؛ Production علاوه بر volume خصوصی پایدار به `ZSH_ATTACHMENT_SCANNER_COMMAND` (JSON argv) نیاز دارد و بدون آن fail-fast می‌شود. فقط وضعیت `clean` قابل دانلود است؛ وضعیت‌های خطا fail-closed می‌مانند. provider واقعی malware scanner و integration محیط مقصد **NOT VERIFIED** هستند.
- metadata DB و محتوای `ZSH_ATTACHMENT_ROOT` باید سازگار و با هم backup/restore شوند. سیاست ۲ ماه از بارگذاری، legal hold و purge تأییدشده در [سیاست امنیت پیوست](../attachment-security-fa.md) است؛ backupهای قدیمی جداگانه طبق سیاست سازمان منقضی می‌شوند.
- اجرای ایمن، inventory، restore staging و مسیرهای بازیابی رخداد در [راهنمای پشتیبان‌گیری و بازیابی](../backup-restore-fa.md) آمده است. زیرساخت production در این مخزن موجود نیست؛ backup production **NOT VERIFIED** است.
- رویدادهای in-app، provider ایمیل، صف، retry، مجوز recipient و وضعیت aggregate در [سیاست و عملیات اعلان‌ها](../notifications-fa.md) مستند است؛ push/SMS و receipt واقعی تحویل در کد فعلی وجود ندارد.

## لاگ، سلامت و عیب‌یابی

`LOGGING` console-based و Django/leave سطح INFO است. Login رویداد audit می‌نویسد و failure پیام generically log می‌شود. سامانه log aggregation، alert، retention و redaction مرکزی را در مخزن پیکربندی نکرده است. `/api/v1/health/` وضعیت زنده را می‌دهد؛ readiness اتصال DB و در production heartbeat زمان‌بند را بررسی می‌کند. API مدیر `/api/v1/admin/jobs/` وضعیت زمان‌بند و اجرای امن Jobها را نشان می‌دهد. استقرار زمان‌بند systemd در [راهنمای عملیات پس‌زمینه](../background-jobs-fa.md) آمده است؛ این نمونه در production این محیط اجرا/تأیید نشده است.

| مشکل | بررسی |
|---|---|
| برنامه با تنظیمات development اجرا شده است | `ZSH_ENV` را در محیط همان process روی `production` بگذارید یا `DJANGO_SETTINGS_MODULE=config.settings_prod` را صریح تعیین کنید؛ `config.settings` در نبود `ZSH_ENV` به development برمی‌گردد |
| شکست ستون یا schema SQLite | دیتابیس disposable بسازید و migration/test runner صحیح اجرا کنید؛ schema را دستی patch نکنید |
| API 403/404 | نقش، scope RoleAssignment، EmployeeProfile، شرکت و manager snapshot را بررسی کنید |
| 409 سهمیه | دفترکل و projection را از طریق سرویس/reconciliation مجاز بررسی؛ Event را مستقیم اصلاح نکنید |
| نشست/CSRF | cookie jar، `X-CSRFToken`, HTTPS و same-site/proxy را بررسی کنید |
| upload ناموفق | فایل غیرخالی و حداکثر ۱۰ MiB باشد؛ پسوند/Content-Type/signature را تطبیق دهید و دسترسی `MEDIA_ROOT` را بررسی کنید |

## استقرار و عملیات بازیابی

Docker/Compose، CI/CD، reverse proxy و فرآیند restore عملی در مخزن وجود ندارد. نمونه systemd timer برای زمان‌بند وجود دارد؛ نصب و فعال بودن آن در محیط مقصد همچنان مسئولیت deployment است. `gunicorn` در dependency است. نمونه فرمان اجرای WSGI با فرض آماده‌بودن environment و DB:

```bash
cd backend
ZSH_ENV=production gunicorn config.wsgi:application --bind 0.0.0.0:8000
```

این نمونه جایگزین تنظیم TLS/reverse proxy، secret manager، media persistence، migration sequencing، backup/restore، health probes یا تأیید استقرار نیست. پیش از deploy، مسئول هر مورد و روش آزمون آن را تعیین کنید. سند [۰۹](09-production-readiness-checklist.md) gateهای پذیرش را فهرست می‌کند.
