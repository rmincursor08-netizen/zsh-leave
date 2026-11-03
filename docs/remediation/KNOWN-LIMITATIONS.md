# محدودیت‌ها و تصمیم‌های باز

## وضعیت مستقل فعلی — ۲ نوامبر ۲۰۲۶

**این وضعیت مستقل مربوط به snapshot مورخ ۲ نوامبر ۲۰۲۶ است، نه وضعیت جاری کامل.** PostgreSQL 17.11 full suite آن snapshot مستقل اجرا شد (۳۷۸ تست، صفر skip). یافتهٔ «scope union بین HR و manager حل نشده» با تصمیم مصوب و پیاده‌سازی F-04 superseded شده است؛ مرجع جاری [سیاست مجوزدهی](../authorization-policy.md) است. دیگر محدودیت‌های ذکرشده نیز وضعیت همان snapshot را توصیف می‌کنند و باید با اسناد remediation جاری سنجیده شوند. شواهد: [ممیزی نهایی](./BACKEND-FINAL-AUDIT.md)، [امنیت](./SECURITY-VERIFICATION.md)، [PostgreSQL](./POSTGRESQL-EVIDENCE.md)، [عملیات](./PRODUCTION-OPERATIONS.md).

این فهرست وضعیت را از کد/مستندات مشاهده‌شده جدا می‌کند؛ موارد «بررسی‌نشده» به معنی پیاده‌سازی سالم یا معیوب نیستند.

## یافته‌های قطعی در snapshot قدیمی — موارد متناقض superseded هستند

- تنظیم زمان‌بندی و اجرای Job، persistence، API وضعیت و نمونه systemd timer پیاده‌سازی شده‌اند؛ نصب/فعال‌بودن timer، alerting و آزمون production هنوز تأیید نشده است. rollover و expiration پیش‌فرض خاموش‌اند تا سیاست سازمانی تصویب شود.
- ادعای خالی بودن `/api/v1/reports/bradford` مربوط به snapshot قبلی است؛ endpoint فعلی پیاده‌سازی شده ولی خروجی آن `provisional` و نیازمند تصویب سیاست است.
- ادعای نبود export route مربوط به snapshot قبلی است؛ APIهای export فعلی route و regression test دارند.
- ادعای اجرا نشدن PostgreSQL مربوط به snapshot قبلی است؛ checkout جاری با PostgreSQL 17.11 full suite آزموده شد.
- هشدار SQLite برای `PublicHoliday` constraint با `nulls_distinct` باقی است.

## بررسی‌نشده یا ناقص در snapshot قدیمی — وضعیت جاری در گزارش نهایی

- بررسی مؤثر بودن end_date در authorization، قطع نشست در offboarding و rehire.
- invitation امن، activation، password setup و revocation.
- ممیزی RBAC همه endpointها، global LeaveType configuration، سلسله‌مراتب company/department/manager و عملیات Site.
- کاربری/سیاست Bradford، دسترسی اختیاری settings و UI loading/error در roleهای متفاوت.
- alerting/retention متمرکز و استقرار واقعی systemd هنوز تأیید نشده است.
- append-only بودن database-level و coverage حسابرسی mutationها.
- PostgreSQL سازگاری queryها، مهاجرت‌ها و triggerهای کامل.
- CORS، proxy trust، TLS واقعی، backup/restore، monitoring، media persistence و deployment واقعی.
- E2E مرورگر و lint اختصاصی.
- API ساعت مرخصی در `client_policy` خاموش (`hourlyLeaveEnabled=false`) است؛ frontend منطق نمایشی/محاسباتی دارد، اما قابلیت production اثبات نشده.
- تنظیمات business مانند دوره سال مرخصی، قواعد Bradford و retention همچنان به تصمیم سازمان نیاز دارند؛ مستند تصمیمات موجود را بررسی کنید.

## گام بعد

ادامه از `PROGRESS.md`: فاز ۳، یکپارچه‌سازی تعریف وضعیت مؤثر استخدام با authorization مشترک و افزودن regression test برای end date گذشته/آینده، بدون غیرفعال‌کردن دسترسی زودهنگام برای end date آینده.
