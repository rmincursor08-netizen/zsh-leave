# امنیت، ذخیره‌سازی و نگهداری پیوست‌های HR

## دامنه و چرخهٔ فایل

پیوست درخواست مرخصی فقط از API احراز هویت‌شده بارگذاری می‌شود. کارمند تنها برای درخواست خودش و HR فقط در scope مجاز سازمانی می‌تواند پیوست را بارگذاری، فهرست یا دانلود کند. مدیر درخواست می‌تواند درخواست را ببیند، اما مجوز مشاهدهٔ پیوست پزشکی/HR را از این دسترسی نمی‌گیرد. درخواست خارج از scope در API به‌صورت 404 پنهان می‌شود و شناسهٔ تصادفی یا URL به‌تنهایی مجوز دسترسی نیست.

API فایل را به PDF، PNG و JPEG محدود می‌کند؛ حجم صفر یا بیش از ۱۰ MiB رد می‌شود. پسوند، MIME ارسالی و نام فایل ورودی قابل اعتماد نیستند: نام Unicode نرمال و کنترل‌کاراکتر/مسیر رد می‌شود؛ MIME باید با نوع مورد انتظار سازگار باشد؛ PDF با parser و ساختار سند و تصویر با decoder واقعی و ابعاد محدود بررسی می‌شود. ZIP، archive و فرمت‌های Office پذیرفته نمی‌شوند؛ بنابراین استخراج archive انجام نمی‌شود. فایل نامعتبر یا ناقص پیش از ذخیره رد می‌شود.

نام روی دیسک تصادفی و مستقل از نام کاربر است. فایل در `ZSH_ATTACHMENT_ROOT` خارج از repository و `MEDIA_ROOT` ذخیره می‌شود؛ directoryها با مجوز `0700` و فایل با `0600` ساخته می‌شوند. URL عمومی storage وجود ندارد و دانلود فقط از endpoint مجازشده می‌گذرد. پاسخ، `Content-Disposition: attachment`، `X-Content-Type-Options: nosniff` و `Cache-Control: private, no-store` دارد. نام اصلی فقط برای نمایش به کاربر مجاز نگهداری می‌شود و در log یا audit snapshot ثبت نمی‌شود.

چرخهٔ Upload → Store → Scan → Download → Delete به‌ترتیب چنین است:

1. پس از بررسی اندازه، نوع، parser و نام، نام تصادفی ساخته و فایل در storage خصوصی نوشته می‌شود؛ metadata با `pending_scan` و Audit ثبت می‌شود.
2. اسکن در تراکنش دارای قفل ردیف به `scanning` می‌رود؛ هر تلاش و زمان شروع/پایان و نتیجهٔ محدود و غیرحساس در فیلدهای مدل و Audit ثبت می‌شود. اسکنر باید پاسخ معتبر بدهد تا وضعیت `clean` شود.
3. فقط رکورد `clean` و `active` از endpoint دارای همان کنترل مالکیت/scope فعلی دانلود می‌شود. هر وضعیت دیگر یا فایل حذف‌شده 404 است.
4. `rejected` در storage خصوصی قرنطینهٔ منطقی می‌شود و تحت همان retention دوماهه باقی می‌ماند؛ purge تأییدشدهٔ فعلی آن را نیز طبق سیاست حذف می‌کند. فایل پاک‌شده وضعیت اسکن `deleted` می‌گیرد.

اگر ثبت DB/audit اولیه شکست بخورد، فایل تازه‌نوشته‌شده پاک می‌شود؛ اگر cleanup خود نیز شکست بخورد، خطای عمومی و بدون مسیر ثبت می‌شود تا orphan در عملیات بررسی شود. purge نیز دو مرحله‌ای است: وضعیت `pending` و Audit ابتدا ثبت می‌شوند، سپس فایل حذف و وضعیت نهایی ثبت می‌شود. در شکست بین مراحل، download بسته می‌ماند و فرمان purge پس از بررسی قابل تکرار است.

## اسکن بدافزار

در Production، `ZSH_ATTACHMENT_SCANNER_COMMAND` الزامی است؛ settings در نبود آن، executable ناموجود/غیرقابل‌اجرا یا نبود `prlimit` fail-fast می‌شود. مقدار متغیر باید JSON array از executable مطلق و آرگومان‌های جدا باشد، نه رشتهٔ shell؛ نمونهٔ شکل پیکربندی:

```ini
ZSH_ATTACHMENT_SCANNER_COMMAND=["/usr/local/libexec/zsh-attachment-scan","--stdin"]
ZSH_ATTACHMENT_SCANNER_TIMEOUT_SECONDS=30
```

فرمان از stdin بایت‌های فایل را می‌خواند، بدون دسترسی به نام فایل کاربر. فقط exit code صفر و stdout شامل دقیقاً یکی از پاسخ‌های UTF-8 معتبر `{"status":"clean"}` یا `{"status":"rejected"}` پذیرفته می‌شود. timeout، خروج غیرصفر، executable/اسکنر غایب، crash، پاسخ خراب/اضافی یا نتیجهٔ ناشناخته هرگز clean نیست؛ وضعیت به‌ترتیب `scanner_unavailable` یا `scan_failed` می‌شود. اجرا با `shell=False`، timeout دیواری، CPU برابر timeout، فایل خروجی حداکثر 64 KiB، حداکثر 64 file descriptor و غیرفعال‌بودن core dump محدود می‌شود. اسکنر/adapter سازمانی باید خود نیز در محیط محدودشدهٔ منابع اجرا شود.

`ZSH_ATTACHMENT_SCANNER` با dotted path فقط برای توسعه و تست سازگاری نگه داشته شده و در Production جایگزین فرمان محدودشده نیست. نبود اسکنر در توسعه فایل را فقط با وضعیت `scanner_unavailable` ذخیره می‌کند؛ این فایل هرگز قابل دانلود نیست. پاسخ Upload برای وضعیت غیر clean، HTTP 202 و وضعیت اسکن را برمی‌گرداند. Uploadهای pending/failed/unavailable را می‌توان با فرمان زیر دوباره اسکن کرد؛ نتیجهٔ نهایی `clean` یا `rejected` با اجرای دوباره تغییر نمی‌کند:

```sh
cd backend
DJANGO_SETTINGS_MODULE=config.settings_prod python manage.py scan_attachments --limit 100
DJANGO_SETTINGS_MODULE=config.settings_prod python manage.py scan_attachments --attachment-id 123
```

فرمان retry را با همان EnvironmentFile production و کاربر مجاز سرویس اجرا کنید؛ خروجی فقط تعداد رکوردهاست. این repository فقط قرارداد adapter را پیاده‌سازی می‌کند؛ ClamAV/سرویس سازمانی واقعی، policy signature/update، جداسازی container/cgroup و integration با نمونه‌های آلودگی آزموده نشده‌اند و تا اجرای staging واقعی **NOT VERIFIED** هستند.

## نگهداری و حذف

سیاست فعلی پروژه: مهلت نگهداری **۲ ماه تقویمی از زمان بارگذاری** است. انقضای این مهلت حذف خودکار نیست. پیش از هر حذف باید مبنای قانونی/سازمانی و نیاز نگهداری بررسی شود؛ حذف فقط با تأیید صریح superuser عملیاتی و ثبت دلیل غیرشخصی انجام می‌شود. پیوست دارای legal hold تا زمان آزادسازی ثبت‌شده قابل حذف نیست. رویدادهای افزودن، فعال/آزادسازی hold، آغاز حذف و حذف نهایی در Audit ثبت می‌شوند. metadata، زمان، actor و وضعیت حذف برای ردگیری باقی می‌ماند؛ محتوای فایل پس از purge قابل بازیابی از مسیر برنامه نیست.

ابتدا فقط فهرست شناسه‌های واجد شرایط را ببینید؛ این فرمان فایل را حذف نمی‌کند:

```sh
cd backend
DJANGO_SETTINGS_MODULE=config.settings_prod python manage.py manage_attachments --action list-expired
```

hold و آزادسازی hold:

```sh
DJANGO_SETTINGS_MODULE=config.settings_prod python manage.py manage_attachments --action hold --attachment-id 123 --actor ops-superuser --reason "legal preservation" --approve
DJANGO_SETTINGS_MODULE=config.settings_prod python manage.py manage_attachments --action release-hold --attachment-id 123 --actor ops-superuser --reason "hold released" --approve
```

پس از بازبینی مستقل و تأیید مجاز، purge:

```sh
DJANGO_SETTINGS_MODULE=config.settings_prod python manage.py manage_attachments --action purge --attachment-id 123 --actor ops-superuser --reason "approved retention deletion" --approve
```

مقدار reason نباید حاوی نام، شناسهٔ شخصی، شرح پزشکی یا خود سند باشد. این فرمان عملیات گروهی/خودکار انجام نمی‌دهد. سیاست ۲ ماهه باید پیش از production توسط HR، Legal و مالک سوابق تأیید شود؛ اگر قانون یا hold سازمان مدت طولانی‌تری لازم می‌کند، hold را قبل از purge اعمال کنید و تنظیم سیاست را فقط پس از تصمیم رسمی تغییر دهید.

## تنظیم، مهاجرت و پشتیبان‌گیری

در production مقدار مطلق و خارج از repository برای `ZSH_ATTACHMENT_ROOT` الزامی است. volume یا private object storage باید فقط برای workerهای برنامه و سرویس backup قابل دسترسی باشد؛ آن را به static/media web server، CDN یا مسیر اجرای فایل متصل نکنید. storage فعلی filesystem خصوصی است؛ private object-storage adapter در repository موجود نیست.

Migration `0019_attachment_security` فایل‌های موجود زیر `MEDIA_ROOT` را به storage خصوصی **کپی** می‌کند و SHA-256 نسخه‌ها را تطبیق می‌دهد؛ منبع قدیمی را حذف نمی‌کند. اگر فایل تاریخی مفقود یا مقصد با محتوای متفاوت موجود باشد، migration fail می‌شود تا بررسی دستی انجام شود. فرمان purge پس از تأیید، نسخهٔ private و کپی احتمالی legacy را پاک می‌کند. برای rollout باید دسترسی خواندن `MEDIA_ROOT` قبلی، دسترسی نوشتن `ZSH_ATTACHMENT_ROOT` و فضای کافی برای نسخهٔ موقت/کپی فراهم باشد. پیش از migration از DB و هر دو محل فایل backup بگیرید؛ حذف دستی legacy copy تا پایان بررسی backup/restore ممنوع است.

تا زمان حذف نسخهٔ legacy طبق retention، هیچ web server یا proxy نباید `MEDIA_ROOT` را به‌عنوان `/media/` یا مسیر عمومی سرو کند؛ دسترسی filesystem آن را نیز محدود کنید. مخزن برنامه route رسانه‌ای برای پیوست ندارد، اما پیکربندی deployment واقعی در این repository مشخص نشده است.

Backup باید **در یک نقطهٔ زمانی سازگار** هم metadata دیتابیس (`pg_dump`) و هم کل محتوای `ZSH_ATTACHMENT_ROOT` را شامل شود. رمزگذاری با کلید جدا از backup، محدودیت دسترسی، retention backup، و آزمون restore به محیط جدا الزامی‌اند. پس از restore، تعداد attachmentهای active و وجود/اندازهٔ هر فایل باید تطبیق داده شوند؛ backup فقط DB یا فقط فایل‌ها قابل بازیابی کامل نیست. حذف از برنامه، backupهای قبلی را فوراً پاک نمی‌کند؛ چرخهٔ انقضا و حذف backup باید جداگانه طبق سیاست سازمان تنظیم شود.
