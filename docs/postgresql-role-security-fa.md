# نقش‌های PostgreSQL و حفاظت تاریخچه

## وضعیت و مرز اعتماد

پیکربندی production برنامه PostgreSQL را انتخاب می‌کند، اما این repository به DB production دسترسی ندارد. bootstrap، Migration 0021، sync مجوزها و probe با credential واقعی `zsh_app` روی PostgreSQL آزمایشی مستقل موفق شده‌اند؛ آزمون روی DB production همچنان **NOT VERIFIED** است. موفقیت تست با SQLite یا با `postgres`/superuser به‌تنهایی حفاظت Runtime را اثبات نمی‌کند.

triggerهای migrationها برای دفاع در عمق UPDATE/DELETE ردیفی را مسدود می‌کنند؛ به آن‌ها برای جلوگیری از `TRUNCATE` اتکا نمی‌شود و trigger عمومی statement-level اضافه نشده است، چون Django test database برای `flush` از دستورات پاک‌سازی سطح جدول استفاده می‌کند. کنترل اصلی این است که Runtime مالک جدول/Schema نباشد و privilegeهای `TRUNCATE`, `TRIGGER`, DDL را نداشته باشد.

### مدل‌ها و جدول‌های تاریخچه

| جدول | مالک مورد انتظار | trigger فعلی | مجوز Runtime | مسیر نوشتن برنامه |
|---|---|---|---|---|
| `leave_auditevent` | `zsh_schema_owner` | `leave_auditevent_append_only`؛ رد UPDATE/DELETE | SELECT, INSERT | `audit_services.record_audit()` از سرویس‌های احراز هویت، منابع HR، مرخصی، export، دعوت، entitlement و job؛ Migration 0004 نیز دادهٔ آغازین ثبت می‌کند. |
| `leave_entitlementevent` | `zsh_schema_owner` | `leave_entitlement_event_immutable_trigger`؛ رد UPDATE/DELETE | SELECT, INSERT | `entitlement_services` برای تخصیص، مصرف، رزرو، اصلاح و reversal؛ backfill در migration 0007. |
| `leave_calendarholidayhistory` | `zsh_schema_owner` | `leave_calendarholidayhistory_append_only`؛ رد UPDATE/DELETE | SELECT, INSERT | `calendar_services` هنگام تغییر تقویم. |
| `leave_leaverequestevent` | `zsh_schema_owner` | `leave_leaverequestevent_append_only`؛ رد UPDATE/DELETE | SELECT, INSERT | `services` و گردش‌کار مرخصی. |
| `leave_leaverequestcomment` | `zsh_schema_owner` | `leave_leaverequestcomment_append_only`؛ رد UPDATE/DELETE | SELECT, INSERT | `services.add_comment` و endpoint نظر درخواست. |
| `leave_leavepolicyversion` | `zsh_schema_owner` | `leave_leavepolicyversion_append_only`؛ رد UPDATE/DELETE | SELECT, INSERT | `calendar_services`؛ نسخهٔ تازه به‌جای ویرایش نسخهٔ جاری. |
| `leave_publicholidayhistory` | `zsh_schema_owner` | `leave_publicholidayhistory_append_only`؛ رد UPDATE/DELETE | SELECT, INSERT | مسیرهای مدیریت تعطیلی عمومی هنگام ایجاد، ویرایش یا حذف منطقی. |
| `leave_jobeffect` | `zsh_schema_owner` | `leave_jobeffect_append_only` در migration 0021؛ رد UPDATE/DELETE | SELECT, INSERT | `job_services` با `get_or_create` برای جلوگیری از اجرای دوبارهٔ اثر job؛ marker موجود ویرایش/حذف نمی‌شود. |

`JobExecution`، `SchedulerHeartbeat`، `NotificationDelivery`، دعوت‌ها و `WorkflowIdempotency` دادهٔ عملیاتی mutable هستند، نه append-only؛ idempotency رکورد workflow را پس از ساخت برای ثبت `resource_id` به‌روزرسانی می‌کند. Runtime برایشان CRUD معمول نیاز دارد. ORM برای `_AppendOnly`، `EntitlementEvent` و `LeavePolicyVersion` تغییر/حذف را رد می‌کند؛ row triggerها دفاع دیتابیسی دیگری هستند و ACL مالکیت/مجوز نیز برای `TRUNCATE` و DDL لازم است.

### آیا Audit معتبر و قابل انتساب است؟

**خیر، نه در برابر compromise شدن برنامه یا credential آن.** Runtime برای مسیرهای معمول برنامه باید `INSERT` روی `AuditEvent` داشته باشد و با نقش دیتابیس مشترک، PostgreSQL هویت کاربر HTTP را نمی‌شناسد. کد `record_audit` actor را از caller می‌گیرد؛ بنابراین کد/فرایند مخرب با credential Runtime می‌تواند actor موجود یا `NULL` را درج کند. ORM برای `created_at` از زمان برنامه مقدار می‌نویسد، اما Runtime قادر به SQL مستقیم است و می‌تواند timestamp دلخواه ثبت کند. سیاست ACL جلوی بازنویسی/حذف/پاک‌سازی ردیف‌های قبلی را می‌گیرد، نه درج رویداد جعلی. از این رو Audit، log تغییرناپذیر در برابر نقش Runtime است، نه مدرک رمزنگاری‌شدهٔ عدم‌انکار یا منبع مستقل حقیقت.

مالک schema، DBA/superuser یا هر نقشی که بتواند مالکیت/trigger/DDL را تغییر دهد نیز می‌تواند تاریخچه را دستکاری کند. این طراحی ادعای مقاومت در برابر DBA ندارد؛ برای آن به ارسال مستقل و tamper-evident خارج از این DB نیاز است.

## نقش‌ها و privilegeها

| نقش | Login | عضویت/استفاده | هدف و مجوز |
|---|---|---|---|
| DBA/cluster administrator | مدیریت‌شده خارج از برنامه | هیچ‌گاه در Web/Jobs | فقط برای ساخت roleها، bootstrap/تغییر مالکیت، rotation و عملیات اضطراری؛ credential جدا و دسترسی auditشده. |
| `zsh_schema_owner` | خیر، `NOLOGIN` | `zsh_migrator` فقط با `SET ROLE` | مالک DB اختصاصی، schema `public`، جدول‌ها، sequenceها، functionها و triggerهای برنامه؛ migration و DDL. |
| `zsh_migrator` | بله، فقط release | عضو `zsh_schema_owner` با `NOINHERIT` | ورود release؛ migration با `PGOPTIONS=-c role=zsh_schema_owner`. هرگز در Web/Jobs استفاده نشود. |
| `zsh_app` | بله | بدون هیچ role membership؛ `NOINHERIT` | فقط `CONNECT`, schema `USAGE`; CRUD روی جدول‌های mutable برنامه (`leave_*` به‌جز هشت جدول بالا، `django_session`, `zsh_cache`); `SELECT/INSERT/UPDATE` روی `auth_user`; `SELECT/INSERT` روی هشت جدول بالا؛ `USAGE/SELECT` فقط روی sequenceهای جدول‌های قابل درج برنامه. بدون مالکیت، database/schema `CREATE`, `TEMP`, `TRUNCATE`, `TRIGGER`, `REFERENCES`, اجرای functionهای `public`, DDL یا `setval`. |
| `zsh_backup` | فقط اگر backup استفاده می‌کند | بدون membership | `CONNECT`, schema `USAGE`, `SELECT` جدول/sequence؛ بدون نوشتن، مالکیت یا credential در برنامه. |

این طرح DB اختصاصی با schema عملیاتی `public` را فرض می‌کند. اگر DB یا schema با برنامه‌های دیگر مشترک است، این اسکریپت را اجرا نکنید؛ revoke عمومی و تغییر مالکیت باید ابتدا برای آن محیط بازطراحی شود.

## نصب تازه و ارتقا

فرمان‌ها در host مدیریتی اجرا می‌شوند. فایل [`provision-runtime-roles.sql`](../deploy/postgresql/provision-runtime-roles.sql) را فقط DBA و روی DB مختص برنامه اجرا کند؛ role password در فایل یا command line قرار نمی‌گیرد. نام دیتابیس را جایگزین کنید:

```sh
psql -X --set=ON_ERROR_STOP=1 --dbname=zsh \
  --set=db_name=zsh \
  --file=deploy/postgresql/provision-runtime-roles.sql
```

اسکریپت idempotent است: roleهای بدون password را می‌سازد/محدود می‌کند، عضویت `zsh_migrator -> zsh_schema_owner` را برقرار می‌کند، مالکیت schema و اشیای موجود public را به schema owner می‌دهد، `CREATE` از `PUBLIC` را می‌گیرد و اتصال را فقط به roleهای سرویس اعطا می‌کند. اگر `zsh_app` یا `zsh_backup` عضو هر roleی باشند، عمداً متوقف می‌شود؛ DBA باید عضویت ناخواسته را بررسی و حذف کند، نه آنکه خطا را دور بزند.

در نصب تازه، ابتدا database خالی را بسازید و bootstrap role script را اجرا کنید. در ارتقا، ابتدا backup قابل‌بازیابی بگیرید؛ سپس script را با DBA اجرا کنید تا مالکیت موجود از role قبلی خارج شود، بعد migration را اجرا کنید. migration role باید فقط برای release credential داشته باشد:

```sh
cd backend
PGOPTIONS='-c role=zsh_schema_owner' \
ZSH_DB_USER=zsh_migrator \
python manage.py migrate --noinput --settings=config.settings_prod
PGOPTIONS='-c role=zsh_schema_owner' \
ZSH_DB_USER=zsh_migrator \
python manage.py provision_cache_table --settings=config.settings_prod
PGOPTIONS='-c role=zsh_schema_owner' \
ZSH_DB_USER=zsh_migrator \
python manage.py sync_runtime_db_grants --settings=config.settings_prod
```

در اجرا، تمام متغیرهای DB/secret از secret manager یا EnvironmentFile محدود به کاربر سرویس تزریق شوند؛ مقادیر بالا فقط نام کاربر و تنظیم role را نشان می‌دهند. release unit از `/etc/zsh-leave/zsh-leave-migrate.env` استفاده می‌کند. فایل Web/Jobs جداگانه باید `ZSH_DB_USER=zsh_app` و secret Runtime داشته باشد. مجوز فایل environment حداکثر `0600` و owner آن root یا کاربر سرویس مورد اعتماد باشد. Web/Jobs را فقط پس از موفقیت هر سه مرحله و readiness سالم فعال کنید.

`sync_runtime_db_grants` باید با `current_user=zsh_schema_owner` اجرا شود. فرمان ابتدا مجوزهای قبلی `PUBLIC` و Runtime/Backup را از جدول‌ها و functionهای موجود می‌گیرد و privilegeهای `CREATE/TEMP` دیتابیس را از Runtime/Backup حذف می‌کند؛ سپس CRUD را فقط به جدول‌های `leave_*` mutable، `django_session` و `zsh_cache` می‌دهد، روی `auth_user` فقط `SELECT/INSERT/UPDATE`، و روی هشت جدول append-only فقط `SELECT/INSERT`. جدول‌های Django مانند `django_migrations`, `django_admin_log`, `auth_group` و `django_content_type` برای Runtime قابل‌خواندن/نوشتن نیستند؛ backup فقط `SELECT` می‌گیرد. دسترسی sequence Runtime نیز به sequenceهای جدول‌های قابل درج محدود است. default privileges برای جدول‌های آینده به‌صورت fail-closed فقط `SELECT/INSERT` و برای functionها بدون `EXECUTE` است؛ بنابراین هر migration جدیدی که به مجوز دیگری نیاز دارد باید پس از بازبینی به همین مرحلهٔ grant افزوده شود. جدول append-only جدید را هم‌زمان در فهرست فرمان، ACL، مستندات و تست‌های PostgreSQL وارد کنید.

## Rotation

1. ابتدا secret تازه را از طریق کنسول امن PostgreSQL با `\password zsh_app` (و جداگانه `\password zsh_migrator`/`zsh_backup` در صورت استفاده) تنظیم کنید؛ `psql` مقدار را echo یا در history ثبت نمی‌کند.
2. مقدار تازه را در secret manager ثبت کنید، سپس Web/Jobs یا release runner را restart کنید تا اتصال‌های جدید از آن استفاده کنند.
3. موفقیت readiness و عملیات DB را بررسی کنید؛ سپس connectionهای قدیمی را drain کنید. از قرار دادن password در آرگومان `psql`, SQL script, log یا خروجی `systemctl` خودداری کنید.
4. در رخداد افشای credential، ابتدا role مربوط را `NOLOGIN` کنید یا password آن را rotate کنید؛ App را با migration/DBA credential جایگزین نکنید. پس از بازیابی، وضعیت عضویت و ACL را دوباره بررسی کنید.

## آزمون credential واقعی App Role

این آزمون فقط روی DB آزمایشی مستقل با نامی که به `_role_test` ختم شود اجرا می‌شود و Runtime credentials واقعی همان DB را می‌خواهد. تست با PostgreSQL superuser یا SQLite جایگزین آن نیست:

```sh
cd backend
DJANGO_SETTINGS_MODULE=config.settings_prod \
ZSH_DB_NAME=zsh_role_test \
ZSH_DB_USER=zsh_app \
python ../deploy/postgresql/verify-runtime-role.py
```

مقادیر password، host و سایر تنظیمات را از secret manager همان محیط آزمایشی بگیرید. آزمون درج از مسیر واقعی `audit_services.record_audit` را داخل تراکنش انجام می‌دهد و rollback می‌کند؛ PostgreSQL ممکن است مقدار sequence را با وجود rollback جلو ببرد. هرگز روی DB دارای دادهٔ واقعی اجرا نکنید.

پس از ساخت DB آزمایشی، مراحل bootstrap، migration با `zsh_migrator` و `PGOPTIONS`, `provision_cache_table` و `sync_runtime_db_grants` را اجرا کنید. سپس probe بالا باید INSERT معتبر را موفق و UPDATE/DELETE/TRUNCATE/DROP/ALTER/تغییر مالکیت/trigger/CREATE را با `42501` رد کند؛ همچنین owner، schema CREATE و role membership را assert می‌کند. این probe روی PostgreSQL آزمایشی با role واقعی `zsh_app` اجرا شده و محدودیت INSERT جعلی Audit را نیز بازتولید کرده است. مجموعهٔ Django testها نیز با Schema Owner روی DB تازه اجرا شود تا migration و `flush` با triggerهای فعلی سازگار بمانند؛ trigger statement-level برای `TRUNCATE` اضافه نکنید.

## بررسی مجوزها

پس از هر تغییر schema/role و قبل از ترافیک:

```sql
SELECT n.nspname, pg_get_userbyid(n.nspowner) AS schema_owner
FROM pg_namespace n WHERE n.nspname = 'public';

SELECT p.proname, pg_get_userbyid(p.proowner) AS function_owner
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public' AND p.proowner = 'zsh_app'::regrole;

SELECT p.proname, has_function_privilege('zsh_app', p.oid, 'EXECUTE') AS app_can_execute
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public';

SELECT has_database_privilege('zsh_app', current_database(), 'CREATE') AS app_can_create_db,
       has_database_privilege('zsh_app', current_database(), 'TEMPORARY') AS app_can_create_temp;

SELECT c.relname, pg_get_userbyid(c.relowner) AS owner,
       has_table_privilege('zsh_app', c.oid, 'SELECT') AS can_select,
       has_table_privilege('zsh_app', c.oid, 'INSERT') AS can_insert,
       has_table_privilege('zsh_app', c.oid, 'UPDATE') AS can_update,
       has_table_privilege('zsh_app', c.oid, 'DELETE') AS can_delete,
       has_table_privilege('zsh_app', c.oid, 'TRUNCATE') AS can_truncate,
       has_table_privilege('zsh_app', c.oid, 'TRIGGER') AS can_create_trigger
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
ORDER BY c.relname;

SELECT r.rolname AS member, g.rolname AS granted_role, m.admin_option
FROM pg_auth_members m
JOIN pg_roles r ON r.oid = m.member
JOIN pg_roles g ON g.oid = m.roleid
WHERE r.rolname IN ('zsh_app', 'zsh_backup', 'zsh_migrator');

SELECT c.relname AS table_name, t.tgname AS trigger_name,
       pg_get_triggerdef(t.oid) AS definition
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND NOT t.tgisinternal
ORDER BY c.relname, t.tgname;
```

انتظار برای DB و schema `public`: owner برابر `zsh_schema_owner` و Runtime نه owner باشد و نه `CREATE` داشته باشد. برای هر هشت append-only: `can_select=true`, `can_insert=true`, `can_update=false`, `can_delete=false`, `can_truncate=false`, `can_create_trigger=false` و owner برابر `zsh_schema_owner`. Runtime نباید در خروجی membership باشد؛ فقط `zsh_migrator -> zsh_schema_owner` مجاز است. همین کنترل‌ها را با `verify-runtime-role.py` در DB آزمایشی و با role واقعی بررسی کنید.

## عملیات DBA و rollback اضطراری

فقط DBA با change record و maintenance window مجاز است مالکیت، DDL یا trigger را تغییر دهد. قبل از اصلاح ACL، backup قابل بازیابی بگیرید؛ بعد از عملیات مجدداً migration check، grant sync، probe App Role و readiness را اجرا کنید. برای بازیابی مالکیت، ابتدا ترافیک را متوقف و App Role را `NOLOGIN` کنید؛ سپس همان bootstrap script را با DBA اجرا و با queryهای بالا نتیجه را بررسی کنید. به‌جای اعطای `TRUNCATE`, مالکیت به `zsh_app` یا دسترسی superuser به برنامه، مشکل را دور نزنید. هیچ rollbackی نباید history را پاک کند.
