# API (v1) — طراحی اولیه

> **Superseded:** این سند طرح اولیه است و قرارداد عملیاتی فعلی، به‌ویژه مجوزهای LeaveType، در [API Contract](./api/api-contract.md) تعریف می‌شود. از جدول Leave types این سند برای پیاده‌سازی فعلی استفاده نکنید.

پایه: `/api/v1/`، JSON، نشست+CSRF (و توکن برای یکپارچه‌سازی بعدی)، صفحه‌بندی `?page&page_size` (حداکثر ۱۰۰)، مرتب‌سازی `?ordering=`، خطا: `{ "code", "message_fa", "fields": {} }`. مستند OpenAPI از drf-spectacular در `/api/v1/schema/`. مسیرهای Auth پیاده‌سازی شده‌اند؛ سایر مسیرهای این سند طراحی هستند مگر آنجا که در قرارداد API وضعیت اجرا مشخص شده باشد.

| گروه | Endpoint | روش | نقش‌ها | نکات |
|------|----------|-----|--------|------|
| Auth | `/auth/login`, `/auth/logout`, `/auth/me`, `/auth/password` | POST/GET | همه | پیاده‌سازی‌شده؛ نشست، CSRF، محدودسازی ورود و ثبت تلاش ناموفق |
| Account activation | `/auth/activate` | POST | عمومی، با توکن یک‌بارمصرف | تنظیم رمز با validator جنگو، تطبیق ایمیل و محدودسازی نرخ؛ پاسخ خطا عمومی |
| Users/Employees | `/employees/`, `/employees/{id}/` | GET/POST/PATCH | HR، مدیر (فقط خواندنِ زیرمجموعه)، سامانه | scope؛ `siteId` اختیاری و اعتبارسنجی‌شده در نوشتن؛ `siteId` و `siteName` در خروجی |
| Employee invitations | `/employees/{id}/invitation/`, `/invitation/revoke`, `/suspend` | POST | HR دامنه‌دار، مدیر سامانه | ارسال/ارسال مجدد، لغو دعوت یا تعلیق؛ توکن قبلی باطل می‌شود؛ بدون Frontend |
| Organization | `/companies/`, `/departments/`, `/positions/`, `/sites/`, `/sites/{id}/` | GET/POST/PATCH؛ حذف سایت فقط در نبود وابستگی | مدیر سامانه؛ HR شرکتی برای سایت‌های همان شرکت | شرکت والد مستقیم سایت است؛ مالکیت سایت تغییرناپذیر؛ بایگانی با `isActive=false`؛ حذف دارای وابسته ممنوع |
| Contracts | `/employees/{id}/contract-periods/` | GET/POST | HR | عدم هم‌پوشانی بازه |
| Leave types | `/leave-types/` | GET؛ POST/PATCH | Superseded — see current contract | مجوز و فیلترهای فعلی در API Contract |
| Requests | `/leave-requests/` GET/POST؛ `/{id}/` GET/PATCH؛ `/{id}/approve|reject|cancel|withdraw|amend` POST | | طبق ماتریس | پیاده‌سازی Phase D؛ همه نوشتن‌ها Idempotency-Key دارند؛ انتقال، اصلاح، رویداد و Audit اتمیک |
| HR record | `/hr/absences/` POST | | HR دامنه‌دار | ایجاد مستقیم در وضعیت `APPROVED`؛ `Idempotency-Key` الزامی؛ رویداد ثبت و اثر دفترکل در همان تراکنش |
| Comments | `/leave-requests/{id}/comments/` | GET/POST | طرفین مجاز | پیاده‌سازی‌شده؛ ممنوع برای نوع محرمانه غیر HR |
| Event history | `/leave-requests/{id}/events/` | GET | مالک، مدیر snapshot، HR مجاز | پیاده‌سازی‌شده؛ دلیل رویداد محرمانه فقط برای HR |
| Attachments | `/leave-requests/{id}/attachments/` POST؛ `/attachments/{id}/download` GET | | HR/مالک مجاز | هر دانلود ثبت می‌شود |
| Balances | `/balances/?year=&employee=` | GET | کارمند خود؛ مدیر زیرمجموعه؛ HR | |
| Entitlements | `/entitlements/` CRUD؛ `/{id}/adjust` POST؛ `/import/?dry_run=` POST | | HR | adjust با `reason`؛ import با ImportBatch |
| Estimate | `/working-days/estimate?employee&start&end` | GET | کارمند/HR | خروجی برچسب `estimated:true` |
| Coverage | `/coverage/?from&to&department` | GET | مدیر/HR | شمارش بدون افشای نوع |
| Calendar | `/calendar/events/?from&to&scope=me|team|org` | GET | طبق scope | redacted |
| Holidays | `/holidays/` | | مشاهده همه؛ مدیریت HR | import/export گروهی در Backend پیاده‌سازی نشده است |
| Notifications | `/notifications/` GET؛ `/{id}/read` POST | | مالک | |
| Reports | `/reports/balances|absences|trends|process-health|bradford` | GET | HR (scope)؛ ارشد (تجمیعی) | |
| Exports | `/exports/{employees,leave-requests,employee-balances,absences,balances,trends,process-health,bradford}.{csv|xlsx}` | GET | ماتریس خروجی پایین؛ همان دامنه read/report | UTF-8 BOM CSV، XLSX، ضد Formula Injection، Audit، no-store |
| Admin | `/admin/settings/`, `/admin/roles/`, `/admin/jobs/` | | مدیر سامانه | تغییر → Audit |
| Audit | `/audit/events/` | GET | سامانه، HR مجاز | فقط خواندن |
| Health | `/health/live`, `/health/ready` | GET | عمومی (بدون جزئیات حساس) | شامل DB و آخرین اجرای Job |

برای قرارداد جزئی ورودی، پاسخ، خطاها و حسابرسی API، به `api/api-contract.md` مراجعه کنید.

### ماتریس مجوز خروجی

| Dataset | کارمند | مدیر | HR | ارشد | مدیر سامانه |
|---|---|---|---|---|---|
| `employees` | خود | تیم مستقیم (یا `scope=me`) | کارکنان scope | ممنوع | فهرست سراسری کارکنان |
| `leave-requests` | درخواست‌های خود | درخواست‌های تیم مستقیم؛ `scope=team` | درخواست‌های scope | ممنوع | ممنوع |
| `employee-balances` | خود | یک کارمند در scope خود (شناسه از API مانده) | یک کارمند در scope HR | ممنوع | ممنوع |
| `absences`, `balances`, `trends`, `process-health` | ممنوع | ممنوع | تجمیع scope HR | تجمیع scope انتساب‌یافته با حداقل گروه | ممنوع |
| `bradford` | ممنوع | ممنوع | صفحه‌بندی‌شده در scope HR | ممنوع | ممنوع |

پارامتر `employee` فقط داده‌ای را محدود می‌کند که API متناظر از قبل مجاز کرده است؛ فیلتر شرکت/tenant پذیرفته نمی‌شود. `leave-requests` همان مجوز و redaction API درخواست را پیش از ساخت CSV/XLSX اعمال می‌کند. بازه `from`/`to` اختیاری آن حداکثر ۳۶۶ روز است. خروجی CSV حداکثر ۱۰٬۰۰۰ ردیف و XLSX حداکثر ۵٬۰۰۰ ردیف دارد؛ خروجی بزرگ‌تر با 413 رد می‌شود. برای گزارش‌های Bradford، پنجره و صفحه‌بندی همان محدودیت گزارش موجود را دارند.

همه خروجی‌ها نشست احراز هویت‌شده می‌خواهند و scope فقط از مجوزهای فعال سمت سرور به‌دست می‌آید. خروجی‌ها هم‌زمان تولید می‌شوند؛ فایل موقت یا توکن دانلود ایجاد نمی‌شود، بنابراین دانلود مجدد نیاز به احراز هویت و مجوز معتبر دارد. CSV با BOM UTF-8 و CRLF ساخته می‌شود؛ رشته‌های فرمول‌مانند در CSV/XLSX خنثی می‌شوند. پاسخ‌ها `private, no-store` هستند. هر خروجی موفق AuditEvent شامل actor، dataset/format، فیلتر و scope، تعداد ردیف و correlation ID ایجاد می‌کند؛ محتوای ردیف‌ها ثبت نمی‌شود.
