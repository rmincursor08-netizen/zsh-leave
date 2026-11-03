# ماتریس نقش‌ها، عملیات و دامنه مجوز

> **SUPERSEDED:** این ماتریس snapshot قدیمی و غیرمرجع است. سیاست مصوب چندنقشی و ماتریس فعلی فقط در [Authorization and Multi-Role Data Scope Policy](../authorization-policy.md) و [API Contract](../api/api-contract.md) تعریف می‌شوند.

## ۱. منبع مجوز و نقش‌های کشف‌شده

این سند از `leave/authorization.py`, `authorization_services.py`, `role_assignment_views.py`, viewهای واقعی و `zsh_core/permissions.py` استخراج شده است. نقش فرانت‌اند صرفاً برای نمایش route/navigation است.

| نقش | منبع محاسبه | scope | قید/محدودیت اصلی |
|---|---|---|---|
| `employee` | User فعال و EmployeeProfile با `is_employee=True` (نه الزاماً `end_date` خالی) | مالک رکورد | تنها درخواست/مانده خود؛ کاربر بدون پروفایل employee نیست |
| `manager` | EmployeeProfile دارای گزارش مستقیم در همان شرکت | direct reports و snapshot مدیر | تصمیم فقط درخواست snapshot خودش و غیرمحرمانه؛ نقش مدیر از RoleAssignment مستقل نیست |
| `hr` | RoleAssignment فعال | company/site/department/employee | دسترسی تنها در scope؛ Global HR مجاز نیست |
| `admin` | RoleAssignment سراسری؛ superuser نیز در context admin می‌شود | global برای تنظیمات/سازمان/نقش؛ بعضی APIهای سهمیه همه کارمندان | درخواست فردی مرخصی نمی‌بیند؛ اما APIهای تراز/entitlement دسترسی فردی دارند؛ `is_superuser` نیز به‌تنهایی HR نیست |
| `exec` | RoleAssignment فعال | company/site/department | `employee` scope/داده فردی ممنوع؛ گزارش‌ها تجمیعی؛ endpoint مانده نیازمند پروفایل است |

**داده snapshot:** درخواست `manager_snapshot` را در زمان ثبت نگه می‌دارد. تغییر مدیر فعلی به‌طور خودکار درخواست قدیمی را به مدیر جدید منتقل نمی‌کند. دامنه HR در هر درخواست با شرکت/سایت/واحد/کارمند تطبیق می‌شود.

## ۲. ماتریس منبع به منبع

| منبع/API | Employee | Manager | HR | Admin | Exec | محدودیت حساسیت/پاسخ غیرمجاز |
|---|---|---|---|---|---|---|
| `auth/login`, `auth/me`, `auth/logout`, `auth/password` | هر کاربر مجاز | همان | همان | همان | همان | نشست فعال، CSRF برای mutation؛ `auth/me` برای ناشناس 401 |
| `leave-requests` فهرست/جزئیات | مالک | گزارش مستقیم snapshot | scope سازمانی | ممنوع | ممنوع | خارج scope عموماً 404؛ list خالی/فقط scope |
| ایجاد درخواست | خود | نقش مدیر، فقط برای employee خودش به‌عنوان requester؛ API client `employeeId` را نفرستد مگر scope HR | فقط scope HR مجاز | ممنوع | ممنوع | نوع confidential فقط HR؛ API قواعد/تاریخ/هم‌پوشانی را enforce می‌کند |
| PATCH درخواست | مالک یا HR scope | ممنوع | HR scope | ممنوع | ممنوع | پاسخ 403 یا 404 بر اساس دید |
| approve/reject | ممنوع | فقط snapshot، شرکت برابر، غیرمحرمانه و وضعیت مجاز | HR scope | ممنوع | ممنوع | رد و reject withdrawal نیازمند دلیل؛ مجوز و ماشین وضعیت جدا |
| cancel/withdraw | مالک؛ لغو confidential فقط HR | ممنوع | HR scope | ممنوع | ممنوع | لغو Approved وارد `WITHDRAWAL_PENDING` می‌شود |
| `amend` | مالک | ممنوع | HR scope | ممنوع | ممنوع | فقط `APPROVED` و فقط یک اصلاح فعال؛ محرمانه فقط HR |
| comments/events | مالک | snapshot | HR scope | ممنوع | ممنوع | comment و attachment برای درخواست محرمانه فقط HR؛ مالک در تنظیم پیش‌فرض confidential جزئیات نوع نمی‌بیند؛ اگر تنظیم `privacy.employee_sees_own_confidential_type=true` شود فقط نام نوع خودش قابل نمایش است، نه دلیل/comment/پیوست؛ `events` GET در مسیر فردی |
| پیوست/download | بر اساس دید درخواست؛ محرمانه HR-only | به همین ترتیب | HR scope | ممنوع | ممنوع | download private/no-store و nosniff؛ فایل نوع/امضا/حجم کنترل |
| `balances` | خود | دامنه direct report | HR scope | endpoint با `employee=<id>` هر EmployeeProfile را از `_employee_rows` admin می‌پذیرد؛ دسترسی UI نمایش داده نمی‌شود | endpoint نیازمند EmployeeProfile و فقط self/direct reports/HR از `scoped_employees`؛ exec بدون پروفایل 403 | تناقض UI/API و احتمال دسترسی فراتر از تفکیک نقش؛ لازم است با مالک داده تعیین تکلیف شود |
| `entitlements`, adjustment, import | ممنوع | ممنوع | HR scope | مجاز؛ `_employee_rows` برای admin همه کارمندان را برمی‌گرداند | ممنوع | endpoint اصلاح بعد از کنترل نقش و scope به سرویس دفترکل می‌رسد؛ آزمون مستقیم API اصلاح/import در suite موجود یافت نشد و موفقیت runtime این ممیزی را ادعا نمی‌کند |
| `hr/absences` | ممنوع | ممنوع | HR و target در scope | نقش admin تنها HR نیست | ممنوع | ثبت/تأیید اتمیک؛ وضعیت درخواست‌ها حسابرسی می‌شود |
| انواع مرخصی: خواندن | GET فعال/requestable | همان | انواع فعال، شامل محرمانه برای عملیات HR | همه، حتی غیرفعال | GET فعال/requestable | خواندن مستقل از مدیریت است |
| انواع مرخصی: ایجاد/ویرایش/غیرفعال‌سازی سراسری | ممنوع | ممنوع | ممنوع، حتی HR سراسری | فقط با `leave_type.global.manage` | ممنوع | فقط نقش `admin` سراسری یا superuser؛ دلیل الزامی و before/after audit؛ حذف فیزیکی/API حذف پشتیبانی نمی‌شود |
| `employees` GET | endpoint با `_employee_rows`؛ کارمند معمولاً self | direct reports | HR scope | `_employee_rows` محدود، مگر query این view | در صورتی که scope Employee قابل مشاهده باشد | GET رکورد خارج scope 404 |
| `employees` POST/PATCH | ممنوع | ممنوع | HR scope شرکت/واحد | مجاز | ممنوع | ایجاد حساب password unusable؛ کنترل company/dept/manager |
| `companies` | فقط شرکت مربوط به `_employee_rows` | مشابه | شرکت‌های scope | همه؛ CRUD | شرکت مجاز از دامنه | تغییر فقط admin |
| `departments` | دامنه کارمندها | scope کارمندها | scope HR | همه؛ CRUD | دامنه داده | تغییر فقط admin؛ parent در فرم UI مدیریت نمی‌شود |
| `contract-periods` | ممنوع | ممنوع | HR scope | مجاز | ممنوع | هم‌پوشانی و درصد ۰ تا ۱۰۰ کنترل می‌شود |
| `calendar/events?scope=me` | تقویم شخصی | خود | خود (با پروفایل) | بدون EmployeeProfile، به‌عنوان admin alone مجاز نیست | بدون پروفایل، مجاز نیست | فقط PENDING/ESCALATED/APPROVED/WITHDRAWAL_PENDING؛ برچسب بی‌جزئیات |
| `calendar/events?scope=team` | ممنوع مگر manager دارای profile | گزارش‌های مستقیم snapshot | تنها اگر خود manager هم هست | تنها اگر manager است | تنها اگر manager است | company isolation و redaction |
| `calendar/events?scope=org` | ممنوع | ممنوع | HR scope | admin تنها ممنوع | ممنوع | HR مجاز؛ داده فردی با برچسب خنثی/حساس‌زدایی |
| `calendar/holidays` GET | نیازمند EmployeeProfile | همان | همان | admin پذیرفته می‌شود | بدون EmployeeProfile ممنوع | تعطیلات ملی/سازمانی و شرکت/سایت |
| `holidays` legacy mutation | ممنوع | ممنوع | endpoint بسته به `is_admin` (superuser/admin) | admin | ممنوع | UI عمومی route دارد ولی server مجوز را enforce می‌کند؛ حذف soft-deactivate |
| Calendar WorkPattern/Policy GET | employee profile | employee profile | employee profile | employee profile | employee profile | GET شخصی؛ POST ساخت الگو/سیاست فقط admin |
| `coverage` | ممنوع | تیم مستقیم | HR scope | فقط اگر HR یا مدیر باشد؛ admin-alone مجاز نیست | ممنوع | بازه حداکثر ۹۳ روز؛ aggregate شمار/تعداد |
| `reports/{kind}` | ممنوع | ممنوع | scope HR | admin-alone ممنوع | scope exec | aggregate و حداقل گروه؛ بدون شناسه مستقیم شخص |
| Bradford notes | ممنوع | ممنوع | HR scope | admin در `_employee_rows` scope | ممنوع | note با سقف ۴۰۰۰ نویسه؛ API GET/POST |
| notifications/read | رویدادهای قابل مشاهده | رویدادهای مرتبط با خود/team | scope HR | هیچ رویدادی (صفر) | فقط اگر هم employee/manager | actor خودش exclude می‌شود؛ متن بدون دلیل/نوع حساس |
| `/admin/settings/` | ممنوع | ممنوع | ممنوع | مجاز | ممنوع | schema validation، دلیل و audit |
| `/admin/jobs/` | ممنوع | ممنوع | ممنوع | مجاز | ممنوع | پاسخ فعلی `[]`؛ job اجرایی از این API اثبات نمی‌شود |
| `/audit/events/` | ممنوع | ممنوع | فقط audit مربوط به LeaveRequest در scope | حداکثر ۵۰۰ رخداد | ممنوع | Admin همه audit؛ reasonهای confidential در سرویس‌ها خالی/محدود |
| `/admin/role-assignments/` | ممنوع | ممنوع | ممنوع | مجاز | ممنوع | self grant/revoke ممنوع؛ grant/revoke audit؛ آخرین admin محافظت می‌شود |
| `/health/`, `/ready/`, `calendar/convert-date/` | عمومی | عمومی | عمومی | عمومی | عمومی | فقط وضعیت زنده/DB و تبدیل تاریخ؛ اطلاعات غیرحساس |

**یادداشت:** `reports` و چند endpoint مدیریت از `has_role` استفاده می‌کنند، نه scope در شرط اولیه؛ اما querysets/aggregate بعدی محدودند. هرگز این را معادل مجوز عام برای endpointهای دیگر تعمیم ندهید. کد `authorization_context` نقش employee را از `is_employee=True` می‌گیرد و `end_date` را بررسی نمی‌کند؛ اعتبار اشتغال باید جداگانه مدیریت شود. جزئیات behavior ممکن است با تغییر کد عوض شود؛ آزمون RBAC در `backend/tests/test_rbac.py` مرجع قابل اجراست.

## ۳. عملیات اعطا/لغو نقش

- endpoint: `GET/POST /api/v1/admin/role-assignments/`; حذف `DELETE /api/v1/admin/role-assignments/{assignment_id}/`.
- نقش admin تنها مجاز است؛ Client UI وجود ندارد.
- Scope باید با role سازگار باشد. `site`/`department`/`employee` باید متعلق به company تخصیص باشند.
- اعطای نقش به خود، self-revoke و ایجاد assignment فعال تکراری رد می‌شود. تغییرها AuditEvent دارند و context هر درخواست از وضعیت جاری DB ساخته می‌شود.
- revocation آخرین admin global برای actor غیر-superuser ممنوع است؛ عملیات اضطراری با superuser فقط رویه کنترل‌شده سازمانی می‌خواهد.

## ۴. تست‌های دسترسی الزامی

۱. برای هر route فهرست‌شده، sessionهای employee A، employee B، manager snapshot، مدیر جدید، HR scope A/B، admin، exec و ناشناس بسازید.
۲. خواندن و نوشتن جداگانه، scope معتبر و خارج از scope، رکورد محرمانه و payload دستکاری‌شده را امتحان کنید.
۳. 404 را رفتار پنهان‌سازی بدانید؛ تفاوت status code به‌تنهایی اثبات عدم نشت نیست؛ response body، نام، نوع، reason، attachment، calendar، notification و report را بررسی کنید.
۴. نقش/دامنه را در DB تست معتبر بسازید؛ UI hide و API client هرگز دلیل قبولی امنیت نیست.

آزمون‌های فعلی: `tests.test_rbac`, `tests.test_permissions`, `leave.tests.test_authorized_services`, `leave.tests.test_workflow_api`, `leave.tests.test_frontend_api`, `leave.tests.test_calendar`. محدودیت اجرای SQLite/PostgreSQL در سند ۰۲ شرح داده شده است.
