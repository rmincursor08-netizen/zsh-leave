# استقرار، پشتیبان‌گیری، بازیابی

وضعیت: فقط نمونهٔ systemd زمان‌بند در [راهنمای کارهای پس‌زمینه](./background-jobs-fa.md) موجود است. توپولوژی وب production در این مخزن مشخص یا قابل اجرا نیست؛ فایل Compose، تنظیمات reverse proxy، unit وب و محدودیت شبکهٔ upstream وجود ندارند.

## توپولوژی وب و تصمیم TLS

اسناد طراحی به اجزای `db`، `backend` و `proxy` اشاره می‌کنند، اما این فقط معماری پیشنهادی است و نه پیکربندی استقرار پشتیبانی‌شده. مسئول استقرار باید پیش از production یکی از این دو حالت را تعیین کند:

1. **TLS تا خود Django/Gunicorn:** وب‌سرور upstream باید اتصال HTTPS را به WSGI با `wsgi.url_scheme=https` منتقل کند. هدرهای ارسالی کاربر برای تعیین طرح درخواست استفاده نشوند.
2. **TLS در reverse proxy و HTTP خصوصی تا Django:** `ZSH_TRUSTED_PROXY_IPS` را فقط با CIDR آدرس‌های واقعی proxyهای مستقیم پر کنید. proxy باید هدر ورودی `X-Forwarded-Proto` را حذف و خودش آن را دقیقاً به `https` یا `http` تنظیم کند. middleware برنامه این هدر را تنها از peer در CIDR مورد اعتماد می‌پذیرد؛ بقیهٔ درخواست‌ها نمی‌توانند با جعل هدر از redirect HTTPS عبور کنند.

در هر دو حالت، `ZSH_ALLOWED_HOSTS` باید نام میزبان‌های عمومی را دقیق فهرست کند؛ wildcard `*` در production رد می‌شود. `ZSH_CSRF_TRUSTED_ORIGINS` فقط originهای کامل HTTPS موردنیاز، مانند `https://app.example.test`، را بپذیرد؛ scheme HTTP، wildcard و path رد می‌شوند. اگر frontend هم‌مبدأ است، این متغیر را خالی بگذارید.

### الگوی vendor-neutral برای proxy (نیازمند پیاده‌سازی در محصول انتخابی)

این الگو دستور پیکربندی یک محصول خاص نیست؛ syntax آن باید پس از انتخاب proxy به تنظیمات همان محصول تبدیل و در staging آزموده شود:

```text
برای هر درخواست ورودی:
  اعتبارسنجی/حفظ Host اصلی مطابق فهرست دامنه‌های منتشرشده
  حذف X-Forwarded-Proto ارسالی کاربر
  تنظیم X-Forwarded-Proto بر اساس TLS خود اتصال ورودی (https یا http)
  بازنویسی X-Forwarded-For با IP همتای واقعی یا ساخت زنجیرهٔ hopهای کنترل‌شده
  ارسال به upstream فقط از شبکهٔ خصوصی یا Unix socket
```

آدرس peerی که Django در `REMOTE_ADDR` می‌بیند باید داخل یکی از CIDRهای `ZSH_TRUSTED_PROXY_IPS` باشد. دسترسی مستقیم اینترنت عمومی به پورت/socket برنامه را با firewall، شبکهٔ container یا کنترل معادل ببندید؛ فقط فیلتر هدر جایگزین محدودسازی شبکه نیست. برای چند proxy، فقط peer مستقیم را trusted کنید و قرارداد بازنویسی/انتقال scheme و IP را برای تمام hopها مستند کنید.

Redirect HTTPS خاموش نمی‌شود. Health checkهای reverse proxy را از مسیر HTTPS منتشرشده به `/api/v1/health/` یا `/api/v1/ready/` بفرستید تا همان گواهی، Host و مسیر TLS بررسی شود. HTTP به خود برنامه، بدون هدر معتبر proxy، عمداً redirect می‌شود. اگر TLS proxy تنظیم نشده باشد اما upstream با HTTP باشد، redirect loop محتمل است؛ قبل از انتشار باید حالت TLS و CIDR peer با واقعیت شبکه تطبیق داده شوند.

### آزمون smoke پس از انتخاب proxy

دامنه و گواهی واقعی را جایگزین کنید؛ `curl` را بدون `-k` اجرا کنید تا اعتبار گواهی هم بررسی شود:

```sh
curl -sSI https://leave.example.test/api/v1/health/
curl -sSI http://leave.example.test/api/v1/health/
curl -sSI https://leave.example.test/api/v1/ready/
curl -sSI https://leave.example.test/api/v1/health/ -H 'Host: attacker.example'
```

انتظار: HTTPS سلامت `200` و HSTS بدهد؛ HTTP یک redirect به همان مسیر HTTPS بدهد؛ readiness پس از آماده‌شدن DB/scheduler `200` باشد؛ Host ناشناخته `400` بگیرد. درخواست HTTP مستقیم به upstream از شبکهٔ غیرمجاز باید در لایهٔ شبکه مسدود شود. اگر از شبکهٔ تست داخلی قابل دسترسی است، جعل `X-Forwarded-Proto: https` از peer خارج از CIDR نباید redirect را دور بزند. جزئیات تست‌های Django در [راهنمای مدیر سامانه](./final-documentation/06-system-administrator-guide.md) و [چک‌لیست production](./final-documentation/09-production-readiness-checklist.md) آمده است.

## نصب
این مخزن فرمان نصب وب/DB را ارائه نمی‌کند. پیش از تعریف deployment، مسئول عملیات باید image/runtime، WSGI server، proxy/TLS، روش secret injection، شبکهٔ خصوصی upstream، volume فایل‌ها، migration و rollback را انتخاب کند. `.env.example` یا دستورهای فرضی Compose در این مخزن مبنای اجرای production نیستند.

پس از انتخاب runtime، پیکربندی امن email backend برای دعوت و اعلان‌های صف‌شده لازم است؛ متغیرها و وضعیت‌ها در [چرخه عمر حساب کارمند](./account-lifecycle-fa.md) و [سیاست اعلان‌ها](./notifications-fa.md) آمده‌اند.

### مرحلهٔ release برای PostgreSQL و cache مشترک

این مخزن Docker/Compose یا unit وب ندارد؛ unit زیر مسیر release نمونهٔ رسمی systemd است. آن را کنار unit سرویس برنامه نصب کنید. فایل environment باید همان متغیرهای production از جمله `ZSH_SECRET_KEY`، `ZSH_DB_*`، `ZSH_ALLOWED_HOSTS` و `ZSH_ATTACHMENT_ROOT` را داشته باشد و فقط برای root و گروه سرویس قابل‌خواندن باشد.

کاربر اجرایی هر دو مرحلهٔ Migration و Provisioning، کاربر سیستم‌عامل `zsh-leave` است؛ این فرایند از همان فایل environment و virtualenv سرویس jobs استفاده می‌کند. فایل environment باید متغیرهای production شامل `ZSH_SECRET_KEY`، `ZSH_DB_*`، `ZSH_ALLOWED_HOSTS`، `ZSH_ATTACHMENT_ROOT` و فرمان اسکنر `ZSH_ATTACHMENT_SCANNER_COMMAND` را داشته باشد. Web/Jobs نیز باید همان تنظیم Scanner را داشته باشند؛ نمونهٔ فرمت و قرارداد stdout در [Runbook پیوست‌ها](./attachment-security-fa.md) است. در هر release، قبل از شروع یا فعال‌کردن Web Workerها اجرا کنید:

```sh
sudo install -o root -g root -m 0644 deploy/systemd/zsh-leave-release.service /etc/systemd/system/zsh-leave-release.service
sudo systemctl daemon-reload
sudo systemctl start zsh-leave-release.service
sudo systemctl --no-pager --full status zsh-leave-release.service
```

unit به‌ترتیب `migrate --noinput`، `provision_cache_table` و همگام‌سازی Grantها را با `config.settings_prod` اجرا می‌کند. اگر اتصال DB، ساخت جدول یا تنظیم ACL شکست بخورد، unit ناموفق می‌شود؛ Web Workerها را تا موفقیت آن شروع نکنید. فرمان Provisioning مستقل و idempotent است و PostgreSQL advisory lock می‌گیرد؛ آن را فقط به‌عنوان مرحلهٔ release یک‌باره اجرا کنید، نه از entrypoint هر Web Worker. هنگام استفاده از release runner دیگری، همان ترتیب را با همان کاربر سیستم‌عامل سرویس و همان محیط production اجرا کنید:

```sh
python manage.py migrate --noinput --settings=config.settings_prod
python manage.py provision_cache_table --settings=config.settings_prod
python manage.py sync_runtime_db_grants --settings=config.settings_prod
```

### نقش‌های PostgreSQL و append-only

پیش از migration، نقش‌ها و مالکیت را مطابق [Runbook نقش‌ها و تاریخچهٔ PostgreSQL](./postgresql-role-security-fa.md) با DBA تنظیم کنید. release unit از فایل جداگانهٔ `/etc/zsh-leave/zsh-leave-migrate.env` استفاده می‌کند: `ZSH_DB_USER=zsh_migrator`، رمز migration از secret manager، و `PGOPTIONS="-c role=zsh_schema_owner"`. این عضویت فقط اجازهٔ `SET ROLE` می‌دهد، نه ارث‌بری؛ نقش `zsh_app` در فایل محیطی Web/Jobs باید جداگانه تنظیم شود و هیچ عضویتی در نقش مالک نداشته باشد. پس از migration، unit جدول cache را provision و با `sync_runtime_db_grants` مجوزهای runtime/backup را همگام می‌کند؛ هر سه مرحله باید موفق باشند، وگرنه انتشار را متوقف کنید.

پس از بالا آمدن وب، `GET /api/v1/ready/` باید `200` و checkهای `database`، `cache`، پیکربندی Scanner و (در production) `scheduler` را موفق گزارش کند؛ در غیر این صورت `503` می‌دهد. این readiness وجود/قابل‌اجرابودن executable را بررسی می‌کند، نه صحت اتصال یا امضای موتور ضدبدافزار؛ آزمون provider واقعی را در staging انجام دهید. برای عیب‌یابی، لاگ unit را با `sudo journalctl -u zsh-leave-release.service --no-pager` ببینید، اتصال و نقش/مالکیت release را بررسی کنید و در صورت شکست provisioning، unit را پس از رفع خطا دوباره اجرا کنید. readiness cache از کلید تصادفی کوتاه‌عمر برای آزمون set/get/delete استفاده می‌کند؛ کلید عملیات را لمس نمی‌کند.

برای اجرای کارهای زمان‌بندی‌شده، unit و timer systemd و شرط‌های readiness در [راهنمای عملیات پس‌زمینه](./background-jobs-fa.md) را دقیقاً نصب و فعال کنید. بدون این پیکربندی، Jobs API زمان‌بند را پیکربندی‌نشده اعلام می‌کند و readiness production موفق نمی‌شود.

## پشتیبان‌گیری و بازیابی
اسکریپت fail-closed، inventory و runbook فارسی در [راهنمای پشتیبان‌گیری و بازیابی](./backup-restore-fa.md) آمده است. این repository فاقد backup destination/offsite، scheduler/alert و PostgreSQL production است؛ فقط یک drill مصنوعی روی دو container موقت PostgreSQL انجام شد. backup production **NOT VERIFIED** و restore drill روی زیرساخت production **NOT RUN** محسوب می‌شوند. تا restore drill محیط واقعی موفق، هیچ backup production را معتبر اعلام نکنید.

## پیش‌نیازهای شبکه داخلی
گواهی TLS داخلی، SMTP داخلی (OQ-13)، دسترسی ذخیره‌سازی/پشتیبان، DNS، فهرست کاربران اولیه و ساختار سازمانی (OQ-11)، مالک سیاست مرخصی در HR.

در production، `ZSH_EMAIL_ENABLED=1`، `ZSH_EMAIL_HOST`، `ZSH_DEFAULT_FROM_EMAIL` و `ZSH_PUBLIC_BASE_URL` با HTTPS را از secret manager/config امن سرویس تنظیم کنید. اگر ارائه‌دهنده آماده نباشد، دعوت در وضعیت pending می‌ماند و API خطای ارسال را اعلام می‌کند؛ هرگز `ZSH_INVITATION_EXPOSE_DEV_LINKS` را در production فعال نکنید.
