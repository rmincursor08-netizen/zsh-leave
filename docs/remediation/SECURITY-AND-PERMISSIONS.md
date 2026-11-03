# امنیت و مجوزها — وضعیت فعلی ممیزی

## تکمیل ممیزی مستقل — ۲ نوامبر ۲۰۲۶

**یادداشت superseded:** این بخش یک snapshot تاریخی از checkout `9b82f59666398348664944cd04c72921e69594c9` است. برای F-04، مشاهدهٔ زیر دربارهٔ حل‌نشده بودن سیاست چندنقشی و Gate 1 superseded شده است؛ سیاست Union با تأیید مالک سامانه تصویب و پیاده‌سازی شده است. مرجع جاری [سیاست مجوزدهی](../authorization-policy.md) و وضعیت تست آن [SECURITY-VERIFICATION.md](./SECURITY-VERIFICATION.md) است. سایر یافته‌های تاریخی این گزارش را نباید به‌عنوان وضعیت جاری فرض کرد.

ماتریس پایین‌تر محدود به فازهای اولیه است. Audit نهایی روی checkout `9b82f59666398348664944cd04c72921e69594c9`، HR scope، global LeaveType، IDOR، lifecycle، activation، exports، notification confidentiality و PostgreSQL row triggers را با suite کامل بررسی کرد. Employment authorization در `authorization_context` کنترل می‌شود. PostgreSQL `TRUNCATE` همچنان bypass تأییدشده است و Gate 6 **NOT VERIFIED** باقی می‌ماند؛ جزئیات تاریخی در [SECURITY-VERIFICATION.md](./SECURITY-VERIFICATION.md) است.

## اصل اجرای مجوز

Backend مرجع مجوز است. `authorization_context` نقش‌ها را از کاربر فعال، profile و `RoleAssignment` فعال می‌سازد؛ selectorهای `scoped_employees` و `scoped_hr_leave_requests` دامنه را در query اعمال می‌کنند. این سند وضعیت بررسی‌شده فازهای ۱ تا ۳ را ثبت می‌کند و ادعای ممیزی کامل همه APIها نیست.

## ماتریس حداقلی مسیرهای بررسی‌شده

| نقش | Balance | Entitlements | گزارش جمعی | Coverage | ثبت غیبت HR |
|---|---|---|---|---|---|
| کارمند | فقط خود؛ انواع محرمانه حذف می‌شوند | 403 | 403 | 403 | 403 |
| مدیر | self/direct-report در selector؛ نوع محرمانه حذف | 403 | 403 | شمار فقط غیرمحرمانه در زیرمجموعه | 403 |
| HR دامنه‌دار | فقط employeeهای selector؛ داده محرمانه فقط برای employee داخل scope | فقط HR و فقط employeeهای `scoped_hr_employees`؛ personal/team دسترسی خارج assignment به entitlement اضافه نمی‌کند | HR دامنه‌دار، شامل داده محرمانه | دامنه HR | فقط employee داخل scope |
| Executive | selector balance می‌تواند دامنه کارمند خود/تیم داشته باشد اما confidential حذف می‌شود | 403 | فقط aggregate؛ داده محرمانه حذف | 403 | 403 |
| Admin | دسترسی عمومی به balance ندارد؛ selector عادی و 404 | 403 مگر نقش HR scoped مستقل | 403 | براساس نقش واقعی؛ admin تنها کافی نیست | براساس HR assignment واقعی و scope |

این جدول محدود به endpointهای بررسی‌شده است؛ **ماتریس جامع permission برای تمام عملیات هنوز تکمیل نشده است**.

## قواعد confidential اعمال‌شده

- داده درخواست محرمانه در scope مجاز HR قابل مشاهده است؛ مدیر و نقش‌های دیگر نوع/برچسب/مانده آن را از balance و aggregateهای بررسی‌شده نمی‌گیرند.
- گزارش executive از رکوردهای confidential برای absences، trends، process-health و balance aggregate استفاده نمی‌کند.
- Coverage مدیر، `awayCount` را با درخواست‌های غیرمحرمانه محاسبه می‌کند. HR scoped می‌تواند aggregate کامل محدوده خود را ببیند.
- privacy group-size موجود همچنان برای گروه‌های کوچک اعمال می‌شود.
- `LeaveType` taxonomy سراسری و بدون اتصال به شرکت است. scoped HR فقط انواع فعال را برای کار HR می‌خواند و مجوز نوشتن ندارد؛ employee/manager فقط انواع فعال و قابل درخواست را می‌خوانند؛ admin global همه انواع را می‌خواند. `leave_type.global.manage` مختص admin سراسری/superuser است.
- ایجاد/ویرایش/غیرفعال‌سازی LeaveType از API و سرویس ادمین هر دو نیازمند permission و دلیل‌اند؛ AuditEvent append-only ثبت actor، before/after، دلیل، timestamp و `X-Correlation-ID` اختیاری را ذخیره می‌کند. حذف، bulk mutation و import برای LeaveType route پشتیبانی‌شده ندارند.
- routeهای CSV/XLSX export در حال حاضر وجود دارند و در [ممیزی نهایی Backend](./BACKEND-FINAL-AUDIT.md) و آزمون‌های `test_exports` بررسی شده‌اند؛ این خلاف گزارش snapshot اولیه است.

## دامنه و چرخه حساب

کد RoleAssignment از scopeهای global/company/site/department/employee پشتیبانی می‌کند؛ برای HR، grant جهانی مجاز نیست. scopeهای hierarchy و lifecycle با آزمون‌های جاری بررسی شده‌اند؛ session منقضی در درخواست بعدی رد می‌شود، اما ابطال پیش‌دستانهٔ نشست یا refresh/API token پیاده‌سازی نشده است. invitation/activation در آزمون‌های جاری پوشش دارد؛ این برخلاف snapshot اولیه است.

## حسابرسی

ثبت HR یک LeaveRequestEvent و AuditEvent یکتا و rollback اتمیک دارد. ORM و triggerهای PostgreSQL برای UPDATE/DELETE آزموده‌اند؛ TRUNCATE و کنترل privilege/role production شکاف باز هستند، بنابراین append-only کلی تأیید نمی‌شود.
