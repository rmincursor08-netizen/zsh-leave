# گردش‌کار و قواعد کسب‌وکار — شواهد فازهای ۱ و ۲

## وضعیت مستقل فعلی — ۲ نوامبر ۲۰۲۶

`authorization_context()` اکنون `is_employed_on()` را برای پروفایل کارمند اعمال می‌کند؛ direct-service approval توسط manager منقضی رد می‌شود و session موجود پس از end date در درخواست بعدی 401 می‌گیرد و offboard می‌شود. Regressionهای این رفتار در `test_authorized_services.py` و `test_invitations.py` روی full SQLite و PostgreSQL suite اجرا شدند. ابطال proactive نشست‌ها وجود ندارد؛ جزئیات در [گزارش نهایی Backend](./BACKEND-FINAL-AUDIT.md).

## وضعیت‌های دیده‌شده در کد

مدل درخواست مقادیر `PENDING`، `ESCALATED`، `APPROVED`، `REJECTED`، `CANCELLED` و `WITHDRAWAL_PENDING` را می‌پذیرد. قواعد جزئی transition در `zsh_core/workflow.py` قرار دارند؛ این گزارش جای بازبینی کامل همه مسیرهای اصلاح/لغو/بازگشایی نیست.

- درخواست employee با نیاز به تأیید و manager حاضر، به `PENDING` می‌رود؛ نبود manager یا manager غایب می‌تواند `ESCALATED` بسازد.
- ثبت HR در `initial_status` مستقیماً `APPROVED` می‌شود. نوعی که تأیید نمی‌خواهد نیز مستقیم تأیید می‌شود.
- لغو درخواست تأییدنشده و درخواست لغو مرخصی تأییدشده مسیرهای جدا دارند؛ وضعیت نهایی و سیاست دقیق همه مسیرها هنوز در فاز workflow کامل بررسی نشده‌اند.

## ثبت غیبت HR اصلاح‌شده

`POST /api/v1/hr/absences/`:

1. نقش HR و scope employee بررسی می‌شود.
2. یک `Idempotency-Key` الزامی است؛ payload fingerprint شامل employee، leave type، تاریخ‌ها، روزکاری، دلیل و جانشین است.
3. `submit_request` یک بار اجرا می‌شود. نتیجه برای actor از نوع HR مستقیماً `APPROVED` است؛ endpoint دیگر `approve` دوم اجرا نمی‌کند.
4. یک رویداد `submit` با `from_status=""` و `to_status="APPROVED"` و یک audit `leave.submit` ثبت می‌شود.
5. اثر دفترکل در صورت مشمول‌بودن نوع مرخصی داخل همان تراکنش ثبت می‌شود.
6. درخواست، رویداد، audit، ledger و idempotency با شکست عملیات rollback می‌شوند.
7. replay همان actor/key/payload همان درخواست را برمی‌گرداند؛ ورودی متفاوت با همان key، 409 می‌دهد.

Frontend واقعی از قبل برای `recordAbsence` هدر `Idempotency-Key` می‌فرستاد. نبود هدر اکنون 422 است.

## مانده‌ها و Bradford

مانده balance endpoint از entitlement ledger گرفته می‌شود. آزمون HR absence نشان می‌دهد ایجاد درخواست تأییدشده نوع مشمول یک واحد را به `used` می‌افزاید، و replay آن مصرف را دوبرابر نمی‌کند.

تابع دامنه Bradford در `zsh_core/reports.py` فرمول `S² × D` را با روزهای تقویمی پیاده می‌کند. مسیر گزارش اکنون به سرویس دامنه متصل است، پیش از aggregation به HR scope محدود می‌شود، انواع محرمانه را حذف می‌کند و صفحه‌بندی صریح دارد. تنظیمات باید صریحاً ذخیره و فعال شده باشند؛ داده جزئیِ فاقد جزئیات روزانه امتیاز نمی‌گیرد. چون انتخاب روز تقویمی در برابر روزکاری، تعطیلات، پاره‌وقت و انواع واجد شرایط هنوز تصویب نشده، پاسخ با وضعیت `provisional` است و تکمیل سیاست محسوب نمی‌شود. شرح API و پرسش‌های باقی‌مانده در [سیاست Bradford](../bradford-policy-fa.md) آمده است.

## چرخه استخدام و خاتمه همکاری — وضعیت جاری

**وضعیت:** FIXED AND VERIFIED برای authorization براساس تاریخ مؤثر اشتغال و رد session در درخواست بعدی؛ NOT FULLY VERIFIED برای چرخه کامل استخدام، rehire و policy درخواست‌های باز.

- `EmployeeProfile.start_date` و `end_date` به‌صورت inclusive در `is_employed_on()` بررسی می‌شوند؛ تاریخ مؤثر از timezone سازمانی و `timezone.localdate()` گرفته می‌شود.
- `authorization_context()` برای پروفایل منقضی یا account غیر فعال‌شده نقش employee/manager نمی‌سازد. Regression manager منقضی، کارمند فعال‌نشده و session موجود پس از end date در `test_authorized_services.py` و `test_invitations.py` روی full PostgreSQL suite پاس شدند.
- درخواست بعد از انقضای اشتغال رد می‌شود و مسیر account access، کاربر را در درخواست بعدی offboard می‌کند؛ session-version یا ابطال پیش‌دستانه وجود ندارد.
- تعلیق و offboarding صریح با `account_status` وجود دارد، اما مدل/سرویس دوره‌های اشتغال مجزا و فرآیند rehire با حفظ تاریخچه در scope این remediation تأیید نشد.
- سیاست درخواست‌های باز یا pending هنگام offboarding هنوز نیازمند تصمیم مالک کسب‌وکار است؛ نباید بدون تصمیم، آن‌ها را خودکار رد یا حذف کرد.
- `ContractPeriod` شرایط قرارداد را نگه می‌دارد و منبع اصلی اشتغال مؤثر نیست. این را نباید با وضعیت authorization اشتباه گرفت.
