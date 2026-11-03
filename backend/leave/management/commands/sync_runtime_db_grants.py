from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections, transaction


RUNTIME_ROLE = "zsh_app"
BACKUP_ROLE = "zsh_backup"
SCHEMA_OWNER_ROLE = "zsh_schema_owner"
APP_READ_WRITE_TABLES = frozenset({
    "django_session",
    "zsh_cache",
})
APP_USER_TABLE = "auth_user"
APPEND_ONLY_TABLES = frozenset({
    "leave_auditevent",
    "leave_entitlementevent",
    "leave_jobeffect",
    "leave_calendarholidayhistory",
    "leave_leaverequestevent",
    "leave_leaverequestcomment",
    "leave_leavepolicyversion",
    "leave_publicholidayhistory",
})


class Command(BaseCommand):
    help = "Synchronize least-privilege PostgreSQL grants for runtime and backup roles."

    def handle(self, *args, **options):
        database = connections[DEFAULT_DB_ALIAS]
        if database.vendor != "postgresql":
            raise CommandError("Runtime database grants are supported only on PostgreSQL.")

        with database.cursor() as cursor:
            cursor.execute("SELECT current_user")
            current_user = cursor.fetchone()[0]
            cursor.execute(
                "SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = %s",
                ["public"],
            )
            result = cursor.fetchone()
            cursor.execute(
                """
                SELECT pg_get_userbyid(datdba)
                FROM pg_database
                WHERE datname = current_database()
                """
            )
            database_owner = cursor.fetchone()[0]
            cursor.execute("SELECT current_database()")
            database_name = cursor.fetchone()[0]
        if current_user != SCHEMA_OWNER_ROLE:
            raise CommandError(
                f"Connect as {SCHEMA_OWNER_ROLE!r} using the migration role and "
                f"PGOPTIONS; current_user is {current_user!r}."
            )
        if result is None or result[0] != SCHEMA_OWNER_ROLE:
            raise CommandError(
                f"Schema public must be owned by {SCHEMA_OWNER_ROLE!r}."
            )
        if database_owner != SCHEMA_OWNER_ROLE:
            raise CommandError(
                f"The application database must be owned by {SCHEMA_OWNER_ROLE!r}."
            )

        with database.cursor() as cursor:
            cursor.execute(
                """
                SELECT rolname, rolcanlogin, rolsuper, rolcreatedb,
                       rolcreaterole, rolinherit, rolreplication, rolbypassrls
                FROM pg_roles
                WHERE rolname = ANY(%s)
                """,
                [[RUNTIME_ROLE, BACKUP_ROLE]],
            )
            roles = {row[0]: row[1:] for row in cursor.fetchall()}
            cursor.execute(
                """
                SELECT r.rolname
                FROM pg_auth_members m
                JOIN pg_roles r ON r.oid = m.roleid
                WHERE m.member = %s::regrole
                """,
                [RUNTIME_ROLE],
            )
            runtime_memberships = [row[0] for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT r.rolname
                FROM pg_auth_members m
                JOIN pg_roles r ON r.oid = m.roleid
                WHERE m.member = %s::regrole
                """,
                [BACKUP_ROLE],
            )
            backup_memberships = [row[0] for row in cursor.fetchall()]

        expected_flags = (True, False, False, False, False, False, False)
        if roles.get(RUNTIME_ROLE) != expected_flags:
            raise CommandError(
                f"{RUNTIME_ROLE!r} must be LOGIN, non-superuser, NOINHERIT, "
                "NOCREATEDB, NOCREATEROLE, and NOBYPASSRLS."
            )
        if roles.get(BACKUP_ROLE) != expected_flags:
            raise CommandError(
                f"{BACKUP_ROLE!r} must be LOGIN, non-superuser, NOINHERIT, "
                "NOCREATEDB, NOCREATEROLE, and NOBYPASSRLS."
            )
        if runtime_memberships or backup_memberships:
            raise CommandError(
                "Runtime and backup roles must have no role memberships; "
                f"found runtime={runtime_memberships!r}, backup={backup_memberships!r}."
            )

        with database.cursor() as cursor:
            cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            present_tables = {row[0] for row in cursor.fetchall()}
        runtime_tables = {
            table for table in present_tables
            if table.startswith("leave_") and table not in APPEND_ONLY_TABLES
        } | APP_READ_WRITE_TABLES
        missing_tables = (
            APPEND_ONLY_TABLES | APP_READ_WRITE_TABLES | {APP_USER_TABLE}
        ) - present_tables
        if missing_tables:
            raise CommandError(
                "Required application tables are missing; refusing to synchronize grants: "
                f"{sorted(missing_tables)!r}."
            )

        quote_name = database.ops.quote_name
        runtime_table_list = ", ".join(
            f"public.{quote_name(table)}" for table in sorted(runtime_tables)
        )
        runtime_sequences = self._sequences_for_tables(
            database, runtime_tables | APPEND_ONLY_TABLES | {APP_USER_TABLE}
        )

        with transaction.atomic(using=DEFAULT_DB_ALIAS):
            with database.cursor() as cursor:
                cursor.execute(
                    "REVOKE CREATE, TEMPORARY ON DATABASE "
                    f"{database.ops.quote_name(database_name)} "
                    "FROM zsh_app, zsh_backup"
                )
                cursor.execute(
                    "REVOKE CREATE ON SCHEMA public FROM PUBLIC, zsh_app, zsh_backup"
                )
                cursor.execute("GRANT USAGE ON SCHEMA public TO zsh_app, zsh_backup")
                cursor.execute(
                    "REVOKE ALL ON ALL TABLES IN SCHEMA public "
                    "FROM PUBLIC, zsh_app, zsh_backup"
                )
                cursor.execute(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
                    + runtime_table_list
                    + " TO zsh_app"
                )
                cursor.execute(
                    "GRANT SELECT, INSERT ON TABLE "
                    + ", ".join(
                        f"public.{quote_name(table)}"
                        for table in sorted(APPEND_ONLY_TABLES)
                    )
                    + " TO zsh_app"
                )
                cursor.execute(
                    "GRANT SELECT, INSERT, UPDATE ON TABLE public."
                    + quote_name(APP_USER_TABLE)
                    + " TO zsh_app"
                )
                cursor.execute(
                    "GRANT SELECT ON ALL TABLES IN SCHEMA public TO zsh_backup"
                )
                cursor.execute(
                    "REVOKE UPDATE, DELETE ON TABLE "
                    + ", ".join(
                        f"public.{database.ops.quote_name(table)}"
                        for table in sorted(APPEND_ONLY_TABLES)
                    )
                    + " FROM zsh_app"
                )
                cursor.execute(
                    "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public "
                    "FROM PUBLIC, zsh_app, zsh_backup"
                )
                cursor.execute(
                    "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public "
                    "FROM PUBLIC, zsh_app, zsh_backup"
                )
                if runtime_sequences:
                    cursor.execute(
                        "GRANT USAGE, SELECT ON SEQUENCE "
                        + ", ".join(
                            f"public.{quote_name(sequence)}"
                            for sequence in sorted(runtime_sequences)
                        )
                        + " TO zsh_app"
                    )
                cursor.execute(
                    "GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO zsh_backup"
                )
                cursor.execute(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "REVOKE ALL ON TABLES FROM PUBLIC, zsh_app, zsh_backup"
                )
                cursor.execute(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT SELECT, INSERT ON TABLES TO zsh_app"
                )
                cursor.execute(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT SELECT ON TABLES TO zsh_backup"
                )
                cursor.execute(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "REVOKE ALL ON SEQUENCES FROM PUBLIC, zsh_app, zsh_backup"
                )
                cursor.execute(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, zsh_app, zsh_backup"
                )
                cursor.execute(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT USAGE, SELECT ON SEQUENCES TO zsh_app"
                )
                cursor.execute(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT SELECT ON SEQUENCES TO zsh_backup"
                )

        self._verify_effective_permissions(database)
        self.stdout.write(
            self.style.SUCCESS(
                "Runtime and backup grants synchronized; append-only tables "
                "are SELECT/INSERT-only for the runtime role."
            )
        )

    def _verify_effective_permissions(self, database):
        with database.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    has_schema_privilege('zsh_app', 'public', 'CREATE'),
                    has_database_privilege('zsh_app', current_database(), 'CREATE'),
                    has_database_privilege('zsh_app', current_database(), 'TEMPORARY'),
                    has_table_privilege('zsh_app', 'public.leave_auditevent', 'TRUNCATE'),
                    has_table_privilege('zsh_app', 'public.leave_auditevent', 'TRIGGER'),
                    has_table_privilege('zsh_app', 'public.leave_auditevent', 'UPDATE'),
                    has_table_privilege('zsh_app', 'public.leave_auditevent', 'DELETE'),
                    has_table_privilege('zsh_app', 'public.leave_auditevent', 'INSERT'),
                    has_table_privilege('zsh_app', 'public.leave_auditevent', 'SELECT'),
                    has_table_privilege('zsh_app', 'public.django_migrations', 'SELECT'),
                    has_table_privilege('zsh_app', 'public.auth_group', 'SELECT'),
                    has_table_privilege('zsh_app', 'public.auth_user', 'DELETE')
                """
            )
            permissions = cursor.fetchone()
            for table, expected_table_privileges in {
                "public.auth_user": (True, True, True, False),
                "public.django_session": (True, True, True, True),
                "public.zsh_cache": (True, True, True, True),
                "public.leave_leaverequest": (True, True, True, True),
            }.items():
                cursor.execute(
                    """
                    SELECT has_table_privilege('zsh_app', %s, 'SELECT'),
                           has_table_privilege('zsh_app', %s, 'INSERT'),
                           has_table_privilege('zsh_app', %s, 'UPDATE'),
                           has_table_privilege('zsh_app', %s, 'DELETE')
                    """,
                    [table] * 4,
                )
                actual = cursor.fetchone()
                if actual != expected_table_privileges:
                    raise CommandError(
                        f"Unexpected {table} privileges for zsh_app: {actual!r}."
                    )

            for table in sorted(APPEND_ONLY_TABLES):
                cursor.execute(
                    """
                    SELECT has_table_privilege('zsh_app', %s, 'SELECT'),
                           has_table_privilege('zsh_app', %s, 'INSERT'),
                           has_table_privilege('zsh_app', %s, 'UPDATE'),
                           has_table_privilege('zsh_app', %s, 'DELETE'),
                           has_table_privilege('zsh_app', %s, 'TRUNCATE'),
                           has_table_privilege('zsh_app', %s, 'TRIGGER')
                    """,
                    [f"public.{table}"] * 6,
                )
                actual = cursor.fetchone()
                if actual != (True, True, False, False, False, False):
                    raise CommandError(
                        f"Unexpected append-only privileges for {table}: {actual!r}."
                    )

            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relowner = 'zsh_app'::regrole
                )
                """
            )
            owns_relations = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_proc p
                    JOIN pg_namespace n ON n.oid = p.pronamespace
                    WHERE n.nspname = 'public'
                      AND p.proowner = 'zsh_app'::regrole
                )
                """
            )
            owns_functions = cursor.fetchone()[0]

        expected = (
            False, False, False, False, False, False, False, True, True,
            False, False, False,
        )
        if permissions != expected or owns_relations or owns_functions:
            raise CommandError(
                "Effective runtime privileges do not match the required "
                f"least-privilege policy: permissions={permissions!r}, "
                f"owns_relations={owns_relations!r}, "
                f"owns_functions={owns_functions!r}."
            )

    @staticmethod
    def _sequences_for_tables(database, table_names):
        with database.cursor() as cursor:
            cursor.execute(
                """
                SELECT sequence.relname
                FROM pg_class sequence
                JOIN pg_namespace namespace ON namespace.oid = sequence.relnamespace
                JOIN pg_depend dependency
                  ON dependency.classid = 'pg_class'::regclass
                 AND dependency.objid = sequence.oid
                 AND dependency.refclassid = 'pg_class'::regclass
                JOIN pg_class table_relation ON table_relation.oid = dependency.refobjid
                WHERE namespace.nspname = 'public'
                  AND sequence.relkind = 'S'
                  AND table_relation.relname = ANY(%s)
                """,
                [list(table_names)],
            )
            return {row[0] for row in cursor.fetchall()}
