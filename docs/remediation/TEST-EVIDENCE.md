# شواهد آزمون

## دستورات و نتایج نهایی (۲ نوامبر ۲۰۲۶)

این evidence برای checkout `9b82f59666398348664944cd04c72921e69594c9` است. اجرای مستقیم روی host با exit 1 متوقف شد چون Django نصب نبود؛ بنابراین تمام آزمون‌ها در disposable `python:3.14-slim` اجرا شدند و PostgreSQL در container جداگانهٔ `postgres:17` بود. هیچ production service/credential استفاده نشد.

```sh
docker run --rm -v "$PWD:/workspace" -w /workspace python:3.14-slim sh -eu -c \
	'pip install --no-cache-dir -q -r backend/requirements.txt; cd backend; \
	 DJANGO_SETTINGS_MODULE=config.settings_test python manage.py test --verbosity 1'
# exit 0: 378 tests, 14 skipped (PostgreSQL-only), 0 failures.

docker run --rm --network zsh-final-audit-20261102 \
	-e ZSH_DB_NAME=postgres -e ZSH_TEST_DB_NAME=test_zsh_final_audit \
	-e ZSH_DB_USER=postgres -e ZSH_DB_PASSWORD=audit-only-password \
	-e ZSH_DB_HOST=zsh-final-audit-pg-20261102 -e ZSH_DB_PORT=5432 \
	-v "$PWD:/workspace" -w /workspace python:3.14-slim sh -eu -c \
	'pip install --no-cache-dir -q -r backend/requirements.txt; cd backend; \
	 DJANGO_SETTINGS_MODULE=config.settings_postgres_test python manage.py test --verbosity 0'
# exit 0: 378 tests, 0 skipped, 0 failures; PostgreSQL 17.11.

DJANGO_SETTINGS_MODULE=config.settings_test python manage.py check
DJANGO_SETTINGS_MODULE=config.settings_test python manage.py makemigrations --check --dry-run
python -m compileall -q config leave zsh_core
```

`check`: exit 0. `makemigrations`: exit 0, `No changes detected`. `compileall`: exit 0. Production `check --deploy --tag security` with a varied dummy environment: exit 0, no system-check issues. SQLite reports `models.W047` for unsupported `nulls_distinct`; PostgreSQL enforces it. The focused PostgreSQL command in `BACKEND-FINAL-AUDIT.md` passed 159 tests, zero skips.

Production cache probe on fresh PostgreSQL: before `createcachetable`, API health returned 500 due to missing `zsh_cache`; after provisioning, 20 requests returned 200 and request 21 returned 429. This is a deployment prerequisite, not a migration result.

Static tools: no repository lint/type configuration exists. Generic `black --check` exited 1 (seven reviewed files would be reformatted); default Flake8 exited 1 (150 E501 and one unused import); Mypy exited 1 (four diagnostics: dynamic Django manager base and three optional-profile narrowing warnings). No broad formatting or test weakening was applied. Exact tool results and limitations are in `BACKEND-FINAL-AUDIT.md`.

Exact static commands, from `backend/`:

```sh
black --check leave/authorization.py leave/employment_services.py \
	leave/tests/test_authorized_services.py leave/tests/test_invitations.py \
	leave/tests/test_calendar.py leave/tests/test_backup_restore_command.py \
	leave/tests/test_append_only_integrity.py
flake8 leave/authorization.py leave/employment_services.py \
	leave/tests/test_authorized_services.py leave/tests/test_invitations.py \
	leave/tests/test_calendar.py leave/tests/test_backup_restore_command.py \
	leave/tests/test_append_only_integrity.py --statistics
mypy --ignore-missing-imports leave/authorization.py leave/employment_services.py
```

The test counts below are historical snapshots and are superseded by this section.

## اجرا پس از اصلاحات — snapshot تاریخی

محیط: Python 3.14.2 / Django 5.2.18، Node 24.22.2 / npm 11.19.1. Backend روی SQLite آزمون اجرا شد.

| فرمان | نتیجه واقعی |
|---|---|
| `cd backend && DJANGO_SETTINGS_MODULE=config.settings_test python3 manage.py test` | موفق؛ ۱۸۱ آزمون، ۳ skipped، ۰ شکست |
| `cd backend && DJANGO_SETTINGS_MODULE=config.settings_test python3 manage.py check` | موفق؛ بدون خطای system check |
| `cd backend && DJANGO_SETTINGS_MODULE=config.settings_test python3 manage.py makemigrations --check --dry-run` | موفق؛ `No changes detected` |
| `cd frontend && npm test` | موفق؛ ۳۴ passed، ۰ failed، ۰ skipped |
| `cd frontend && npm run build` | موفق؛ `tsc --noEmit` و Vite production build |
| `git diff --check` | موفق؛ whitespace error گزارش نشد |

سه مورد skipped کلاس `PostgreSQLEntitlementConcurrencyTests` هستند: دو آزمون هم‌زمانی رزرو/تأیید دفترکل و آزمون trigger ضد تغییر. شرط کلاس `connection.vendor == "postgresql"` است.

## آزمون‌های رگرسیون جدید

- `test_confidential_balances_require_hr_scope`: کارمند، مدیر، HR در scope، HR شرکت دیگر و admin؛ confidential balance/labels و `coverage.awayCount`.
- `test_entitlements_hide_confidential_data_outside_hr_scope`: نقش کارمند، HR مجاز و HR خارج scope.
- همان آزمون، HR دارای employee profile خارج از HR assignment را نیز می‌سنجد؛ personal scope نباید به entitlements سازمانی HR اضافه شود.
- `test_executive_reports_exclude_confidential_records_and_balances`: absences، trends، process-health و balances؛ به‌علاوه دسترسی HR به absences در scope.
- `test_hr_absence_is_directly_approved_and_idempotent`: 422 بدون کلید، وضعیت approved، event/audit یکتا، اثر دفترکل و عدم duplicate/replay، و 409 برای payload متفاوت.
- `test_hr_absence_rejects_unauthorized_and_rolls_back_invalid_submission`: 403، 404 خارج دامنه، rollback درخواست/event/audit/idempotency.

## محدودیت شواهد

- PostgreSQL اجرا نشد؛ `pg_isready` نصب نبود و تنظیم اتصال integration test فراهم نبود. موفقیت SQLite معادل تأیید PostgreSQL نیست.
- E2E مرورگر، تست بار، lint مستقل، backup/restore، migration روی PostgreSQL و تست production settings/deployment اجرا نشدند.
- Frontend در این تغییر کد نداشت؛ test/build برای اطمینان از عدم regression مجدداً اجرا شد.
