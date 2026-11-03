"""Verify runtime DB privileges using the configured zsh_app credentials.

Run only against an isolated database whose name ends with ``_role_test``.
"""
import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings_prod")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import django

django.setup()

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import DatabaseError, connection, transaction

from leave.audit_services import record_audit
from leave.management.commands.sync_runtime_db_grants import APPEND_ONLY_TABLES


if not os.environ.get("ZSH_DB_NAME", "").endswith("_role_test"):
    raise SystemExit("Refusing to run: ZSH_DB_NAME must end with _role_test.")


def assert_denied(label, statement):
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(statement)
    except DatabaseError as exc:
        cause = exc.__cause__
        if getattr(cause, "sqlstate", None) != "42501":
            raise AssertionError(
                f"{label} failed for an unexpected reason: "
                f"{getattr(cause, 'sqlstate', None)}"
            ) from exc
    else:
        raise AssertionError(f"{label} unexpectedly succeeded.")


with connection.cursor() as cursor:
    cursor.execute("SELECT current_user, session_user, current_database()")
    current_user, session_user, database_name = cursor.fetchone()
    if current_user != "zsh_app" or session_user != "zsh_app":
        raise SystemExit("Connect with the real zsh_app database credentials.")
    cursor.execute(
        """
        SELECT
            r.rolcanlogin, r.rolsuper, r.rolcreatedb, r.rolcreaterole,
            r.rolinherit, r.rolreplication, r.rolbypassrls,
            pg_has_role('zsh_app', 'zsh_schema_owner', 'MEMBER'),
            has_schema_privilege('zsh_app', 'public', 'CREATE'),
            has_database_privilege('zsh_app', current_database(), 'CREATE'),
            has_database_privilege('zsh_app', current_database(), 'TEMPORARY')
        FROM pg_roles r
        WHERE r.rolname = 'zsh_app'
        """
    )
    role_facts = cursor.fetchone()
    if role_facts != (
        True, False, False, False, False, False, False, False, False, False, False
    ):
        raise AssertionError(f"Unexpected runtime role attributes: {role_facts!r}.")
    cursor.execute(
        """
        SELECT
            (SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = 'public'),
            (SELECT datdba::regrole::text FROM pg_database
             WHERE datname = current_database()),
            EXISTS (
                SELECT 1 FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relowner = 'zsh_app'::regrole
            ),
            EXISTS (
                SELECT 1 FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public' AND p.proowner = 'zsh_app'::regrole
            )
        """
    )
    schema_owner, database_owner, app_owns_relation, app_owns_function = cursor.fetchone()
    if (
        schema_owner != "zsh_schema_owner"
        or database_owner != "zsh_schema_owner"
        or app_owns_relation
        or app_owns_function
    ):
        raise AssertionError(
            f"Unexpected ownership: database={database_owner!r}, "
            f"schema={schema_owner!r}, "
            f"runtime_owns_relation={app_owns_relation!r}, "
            f"runtime_owns_function={app_owns_function!r}."
        )
    cursor.execute(
        """
        SELECT r.rolname
        FROM pg_auth_members m
        JOIN pg_roles r ON r.oid = m.roleid
        WHERE m.member = 'zsh_app'::regrole
        """
    )
    memberships = [row[0] for row in cursor.fetchall()]
    if memberships:
        raise AssertionError(f"Runtime role has unexpected memberships: {memberships!r}.")

    for table in sorted(APPEND_ONLY_TABLES):
        cursor.execute(
            """
            SELECT pg_get_userbyid(c.relowner),
                   EXISTS (
                       SELECT 1 FROM pg_trigger t
                       WHERE t.tgrelid = c.oid AND NOT t.tgisinternal
                   )
            FROM pg_class c
            WHERE c.oid = %s::regclass
            """,
            [f"public.{table}"],
        )
        table_owner, has_trigger = cursor.fetchone()
        if table_owner != "zsh_schema_owner" or not has_trigger:
            raise AssertionError(
                f"Unexpected owner or missing row guard for {table}: "
                f"owner={table_owner!r}, trigger={has_trigger!r}."
            )
        cursor.execute(
            """
            SELECT
                has_table_privilege('zsh_app', %s, 'SELECT'),
                has_table_privilege('zsh_app', %s, 'INSERT'),
                has_table_privilege('zsh_app', %s, 'UPDATE'),
                has_table_privilege('zsh_app', %s, 'DELETE'),
                has_table_privilege('zsh_app', %s, 'TRUNCATE'),
                has_table_privilege('zsh_app', %s, 'TRIGGER')
            """,
            [f"public.{table}"] * 6,
        )
        privileges = cursor.fetchone()
        if privileges != (True, True, False, False, False, False):
            raise AssertionError(
                f"Unexpected {table} privileges for zsh_app: {privileges!r}."
            )

probe_id = uuid.uuid4().hex
with transaction.atomic():
    event = record_audit(
        actor=None,
        origin="system",
        action="runtime_role_probe",
        entity="RuntimeRoleProbe",
        entity_id=probe_id,
        after={"probe": "valid app insert"},
        source_context="isolated-role-test",
    )
    assert event.pk is not None
    table = (
        f"{connection.ops.quote_name('public')}."
        f"{connection.ops.quote_name('leave_auditevent')}"
    )
    event_id = int(event.pk)

    assert_denied(
        "UPDATE",
        f"UPDATE {table} SET action = 'tampered' WHERE id = {event_id}",
    )
    assert_denied(
        "DELETE",
        f"DELETE FROM {table} WHERE id = {event_id}",
    )
    assert_denied("TRUNCATE", f"TRUNCATE TABLE {table}")
    assert_denied(
        "ALTER TABLE",
        f"ALTER TABLE {table} ADD COLUMN role_probe integer",
    )
    assert_denied("DROP TABLE", f"DROP TABLE {table}")
    assert_denied(
        "ALTER OWNER",
        f"ALTER TABLE {table} OWNER TO zsh_app",
    )
    assert_denied(
        "ALTER TRIGGER",
        f"ALTER TABLE {table} DISABLE TRIGGER leave_auditevent_append_only",
    )
    assert_denied(
        "DROP TRIGGER",
        f"DROP TRIGGER leave_auditevent_append_only ON {table}",
    )
    assert_denied(
        "CREATE TRIGGER",
        f"CREATE TRIGGER role_probe BEFORE INSERT ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION public.leave_reject_append_only_mutation()",
    )
    assert_denied(
        "CREATE TABLE",
        "CREATE TABLE public.zsh_role_test_probe (id integer)",
    )
    assert_denied(
        "CREATE TEMP TABLE",
        "CREATE TEMP TABLE zsh_role_test_temp_probe (id integer)",
    )

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM public.leave_auditevent WHERE id = %s",
            [event_id],
        )
        if cursor.fetchone()[0] != 1:
            raise AssertionError("The valid application audit insert was not readable.")

    actor = get_user_model()(
        username=f"role-probe-{probe_id}",
    )
    actor.set_unusable_password()
    actor.save(force_insert=True)
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE public.auth_user SET last_login = CURRENT_TIMESTAMP WHERE id = %s",
            [actor.pk],
        )
    cache_key = f"zsh-role-probe:{probe_id}"
    cache.set(cache_key, probe_id, timeout=30)
    if cache.get(cache_key) != probe_id:
        raise AssertionError("Runtime cache get failed.")
    cache.delete(cache_key)
    if cache.get(cache_key) is not None:
        raise AssertionError("Runtime cache delete failed.")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO public.leave_auditevent
                (actor_id, origin, action, entity, entity_id, before, after,
                 reason, result, correlation_id, source, created_at)
            VALUES (%s, 'system', 'forged_probe', 'RuntimeRoleProbe', %s,
                    NULL, NULL, '', 'ok', '', 'isolated-role-test', %s)
            RETURNING actor_id, created_at
            """,
            [actor.pk, probe_id, "2000-01-01T00:00:00+00:00"],
        )
        forged_actor, forged_timestamp = cursor.fetchone()
        if forged_actor != actor.pk or forged_timestamp.year != 2000:
            raise AssertionError("Expected Runtime audit attribution limitation was not reproduced.")

print(
    f"PASS: zsh_app role permissions verified on isolated database {database_name!r}; "
    "all probe writes rolled back. LIMITATION CONFIRMED: the Runtime role can insert "
    "an AuditEvent with an arbitrary actor and timestamp."
)
