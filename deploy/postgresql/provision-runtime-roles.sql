\set ON_ERROR_STOP on

-- Run only against the dedicated application database as a DBA.
-- Passwords are intentionally managed out-of-band, never embedded here.
SELECT current_database() = :'db_name' AS connected_to_target \gset
\if :connected_to_target
\else
\echo 'Connect to the target application database and set db_name to that same database.'
\quit 3
\endif

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zsh_schema_owner') THEN
        CREATE ROLE zsh_schema_owner NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zsh_migrator') THEN
        CREATE ROLE zsh_migrator LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zsh_app') THEN
        CREATE ROLE zsh_app LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zsh_backup') THEN
        CREATE ROLE zsh_backup LOGIN;
    END IF;
END
$$;

ALTER ROLE zsh_schema_owner
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE zsh_migrator
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE zsh_app
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE zsh_backup
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_auth_members
        WHERE member IN ('zsh_app'::regrole, 'zsh_backup'::regrole)
    ) THEN
        RAISE EXCEPTION
            'zsh_app and zsh_backup must not inherit or be able to SET ROLE to any role';
    END IF;
END
$$;

REVOKE zsh_schema_owner FROM zsh_migrator;
GRANT zsh_schema_owner TO zsh_migrator;
REVOKE CONNECT, TEMPORARY ON DATABASE :"db_name" FROM PUBLIC;
REVOKE ALL ON DATABASE :"db_name" FROM zsh_migrator, zsh_app, zsh_backup;
GRANT CONNECT ON DATABASE :"db_name" TO zsh_migrator, zsh_app, zsh_backup;
ALTER DATABASE :"db_name" OWNER TO zsh_schema_owner;

ALTER SCHEMA public OWNER TO zsh_schema_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC, zsh_migrator, zsh_app, zsh_backup;
GRANT ALL ON SCHEMA public TO zsh_schema_owner;
GRANT USAGE ON SCHEMA public TO zsh_app, zsh_backup;

DO $$
DECLARE
    object_record record;
BEGIN
    FOR object_record IN
        SELECT c.relkind, n.nspname, c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
          AND NOT EXISTS (
              SELECT 1
              FROM pg_depend d
              WHERE d.classid = 'pg_class'::regclass
                AND d.objid = c.oid
                AND d.deptype = 'e'
          )
        ORDER BY CASE WHEN c.relkind = 'S' THEN 1 ELSE 0 END
    LOOP
        CASE object_record.relkind
            WHEN 'S' THEN
                EXECUTE format(
                    'ALTER SEQUENCE %I.%I OWNER TO zsh_schema_owner',
                    object_record.nspname, object_record.relname
                );
            WHEN 'v' THEN
                EXECUTE format(
                    'ALTER VIEW %I.%I OWNER TO zsh_schema_owner',
                    object_record.nspname, object_record.relname
                );
            WHEN 'm' THEN
                EXECUTE format(
                    'ALTER MATERIALIZED VIEW %I.%I OWNER TO zsh_schema_owner',
                    object_record.nspname, object_record.relname
                );
            WHEN 'f' THEN
                EXECUTE format(
                    'ALTER FOREIGN TABLE %I.%I OWNER TO zsh_schema_owner',
                    object_record.nspname, object_record.relname
                );
            ELSE
                EXECUTE format(
                    'ALTER TABLE %I.%I OWNER TO zsh_schema_owner',
                    object_record.nspname, object_record.relname
                );
        END CASE;
    END LOOP;

    FOR object_record IN
        SELECT p.prokind, n.nspname, p.proname,
               pg_get_function_identity_arguments(p.oid) AS identity_args
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.prokind IN ('f', 'p')
          AND NOT EXISTS (
              SELECT 1
              FROM pg_depend d
              WHERE d.classid = 'pg_proc'::regclass
                AND d.objid = p.oid
                AND d.deptype = 'e'
          )
    LOOP
        EXECUTE format(
            'ALTER %s %I.%I(%s) OWNER TO zsh_schema_owner',
            CASE object_record.prokind
                WHEN 'p' THEN 'PROCEDURE'
                ELSE 'FUNCTION'
            END,
            object_record.nspname,
            object_record.proname,
            object_record.identity_args
        );
    END LOOP;
END
$$;
