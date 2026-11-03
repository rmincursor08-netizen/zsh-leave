# PostgreSQL Verification Evidence

**Date:** 2026-11-02. Current independent replay used checkout `9b82f59666398348664944cd04c72921e69594c9`. All PostgreSQL instances described here were disposable Docker containers; no production DB or credentials were used.

## Environment and commands

- Image: `postgres:17`; server reported `17.11 (Debian 17.11-1.pgdg13+2)`.
- Python runner: `python:3.14-slim`; dependencies installed from `backend/requirements.txt` inside the disposable container.
- Backend settings: `config.settings_postgres_test`; database credentials generated randomly and passed via environment variables.

Full suite command executed against the isolated PostgreSQL service (environment variable values were ephemeral):

```sh
docker run --rm --network "$PG_TEST_NETWORK" \
  -e ZSH_DB_NAME=audittest -e ZSH_TEST_DB_NAME=test_audittest \
  -e ZSH_DB_USER=postgres -e ZSH_DB_PASSWORD="$PG_TEST_PASSWORD" \
  -e ZSH_DB_HOST=pgtest -e ZSH_DB_PORT=5432 \
  -v "$PWD/..:/workspace" -w /workspace python:3.14-slim sh -eu -c \
  'pip install --no-cache-dir -q -r backend/requirements.txt; cd backend; \
   DJANGO_SETTINGS_MODULE=config.settings_postgres_test \
   python manage.py test --verbosity 0'
```

**Independently observed on current checkout:** exit 0; `Ran 378 tests`; `OK`; **0 skipped, 0 failures**. Focused authorization/lifecycle/invitation/hierarchy/import/ledger/export/attachment/audit/Bradford/job/notification/workflow/site modules also passed: **159 tests, 0 skips, 0 failures**.

SQLite comparison command used the same disposable Python image with `DJANGO_SETTINGS_MODULE=config.settings_test`: exit 0; **378 tests, 14 PostgreSQL-only skips, 0 failures**. SQLite is not used as PostgreSQL evidence.

## Migration and schema facts

A fresh PostgreSQL test DB was created by Django’s test runner and all migrations applied. Separately, `python manage.py migrate --noinput` on a fresh PostgreSQL 17 database applied **20 leave migrations**, `0001` through `0020_notificationdelivery`; no `0021` migration exists. `makemigrations --check --dry-run` returned exit 0 and `No changes detected`.

Catalog query executed on the migrated database:

```sql
SELECT table_name || '.' || column_name || '=' || data_type || ' max=' || character_maximum_length
FROM information_schema.columns
WHERE table_name='leave_auditevent' AND column_name='entity_id';
```

Observed: `leave_auditevent.entity_id=character varying max=64`. This supports both numeric request IDs and nonnumeric export identifiers. The PostgreSQL test `test_scoped_audit_query_casts_numeric_subquery_to_text` observed the generated `::varchar` cast.

Catalog inspection showed `leave_auditevent_append_only` and `leave_entitlement_event_immutable_trigger` as `BEFORE DELETE OR UPDATE ... FOR EACH ROW` triggers. The full PostgreSQL suite passed direct ORM/raw-SQL UPDATE/DELETE rejection, entitlement triggers/projection guard, `nulls_distinct=False`, timestamp-with-time-zone, and concurrency cases:

- `test_postgresql_triggers_reject_direct_sql_mutations`
- `test_projection_writes_without_ledger_entries_are_rejected`
- `test_parallel_reservations_serialize_without_lost_ledger_entries`
- `test_parallel_approval_replays_one_atomic_posting`
- `test_concurrent_duplicate_imports_commit_at_most_once`
- `test_concurrent_same_slot_executes_once`
- `test_concurrent_manager_updates_cannot_create_a_cycle`
- `test_hr_absence_is_directly_approved_and_idempotent`

The full-suite command above is the exact command context for each test.

## Confirmed append-only gap

The migration triggers block row `UPDATE` and `DELETE`; they are not statement-level `TRUNCATE` guards. This was reproduced on a disposable migrated PostgreSQL database using separate committed statements:

```sh
docker exec "$PG_CONTAINER" psql -X -U postgres -d audittest -c \
  "INSERT INTO public.leave_auditevent (origin, action, entity, entity_id, before, after, reason, result, correlation_id, source, created_at) VALUES ('audit', 'truncate_probe', 'AuditEvent', 'probe-1', NULL, NULL, '', 'ok', '', '', CURRENT_TIMESTAMP)"
docker exec "$PG_CONTAINER" psql -X -U postgres -d audittest -c \
  "TRUNCATE TABLE public.leave_auditevent"
docker exec "$PG_CONTAINER" psql -X -U postgres -d audittest -Atc \
  "SELECT count(*) FROM public.leave_auditevent"
```

Observed: insert succeeded; `TRUNCATE TABLE` succeeded; remaining row count was `0`. A `TransactionTestCase` was used while probing; `TestCase` had produced a false positive because PostgreSQL rejected `TRUNCATE` with pending transaction trigger events. A proposed statement trigger was not retained: it made Django PostgreSQL test database flush fail because flush uses `TRUNCATE`.

**Status at the time of this evidence:** Gate 6 was NOT VERIFIED because production role grants were unavailable. The repository now includes a repeatable role bootstrap, runtime grant synchronization, a separate-role release configuration, and an isolated PostgreSQL App Role probe in [the PostgreSQL role-security runbook](../postgresql-role-security-fa.md). These changes do not constitute evidence that production has applied the grants.

Runtime must be able to INSERT valid AuditEvent rows, so a compromised application process/credential can fabricate actor attribution or choose an arbitrary timestamp via direct SQL. The row triggers do not authenticate event provenance. The ACLs protect existing rows from Runtime UPDATE/DELETE/TRUNCATE, not from DBA/superuser or application-originated false inserts.

## Rollback evidence

`AuditCoverageTests.test_audit_failure_rolls_back_business_mutation` verifies an audit-write exception rolls back a Company mutation. `AppendOnlyIntegrityTests.test_rejected_mutation_rolls_back_without_poisoning_outer_transaction` verifies rejected SQL mutation does not poison an outer transaction. Entitlement/workflow/import tests verify ledger, request, audit and import rollback boundaries. These passed in both full suites.

## Independent Current-Checkout Replay

The original evidence below predates the role-protection implementation and documents the prior state. In a separate disposable PostgreSQL 17 database, the current checkout successfully applied migration `0021_jobeffect_append_only`, synchronized the least-privilege grants as `zsh_schema_owner`, and passed `verify-runtime-role.py` using the actual `zsh_app` credentials. The probe covered all eight append-only tables, INSERT versus UPDATE/DELETE/TRUNCATE/DDL/trigger/ownership denial, role membership, and normal AuditEvent insertion. An upgrade from `0020` preserved an existing audit row. These are isolated test results only; the production role/ACL state remains **NOT VERIFIED**.

On 2026-11-02, a fresh PostgreSQL 17.11 database applied all current migrations, including `leave.0020_notificationdelivery`; `migrate --check` and `makemigrations --check --dry-run` exited 0. `entity_id` was confirmed as `character varying(64)`. The full suite then passed with 378 tests and no skips. All databases were disposable and contained no production data.

The production cache table is not created by Django migrations or repository deployment assets. On the freshly migrated database:

```sql
SELECT to_regclass('public.zsh_cache');
```

returned `NULL`. With `DJANGO_SETTINGS_MODULE=config.settings_prod`, a GET to `/api/v1/health/` returned 500 because DRF throttling queried the missing `zsh_cache` relation. After running `DJANGO_SETTINGS_MODULE=config.settings_prod python manage.py createcachetable zsh_cache`, 20 requests returned 200 and request 21 returned 429. This verifies the configured throttle works once provisioned, while exposing a required deployment step absent from the migration/deployment path.

Exact provisioning and request-loop commands, run from `backend/` with production settings and dummy environment values against the disposable DB:

```sh
DJANGO_SETTINGS_MODULE=config.settings_prod python manage.py createcachetable zsh_cache
DJANGO_SETTINGS_MODULE=config.settings_prod python manage.py shell -c \
  'from django.test import Client; c=Client(raise_request_exception=False); statuses=[c.get("/api/v1/health/", secure=True, HTTP_HOST="localhost").status_code for _ in range(21)]; print(statuses); assert statuses[:20] == [200]*20 and statuses[20] == 429'
```
