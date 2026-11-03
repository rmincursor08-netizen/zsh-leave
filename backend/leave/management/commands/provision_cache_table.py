from django.core.cache import caches
from django.core.cache.backends.db import DatabaseCache
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, DatabaseError, connections


POSTGRES_CACHE_PROVISION_LOCK = 0x5A53484341434845


class Command(BaseCommand):
    help = "Create the configured database cache table safely during a release."

    def handle(self, *args, **options):
        cache = caches["default"]
        if not isinstance(cache, DatabaseCache):
            raise CommandError(
                "The default cache backend must be DatabaseCache to provision its table."
            )

        database_alias = DEFAULT_DB_ALIAS
        database = connections[database_alias]
        lock_acquired = False

        try:
            if database.vendor == "postgresql":
                with database.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_lock(%s)",
                        [POSTGRES_CACHE_PROVISION_LOCK],
                    )
                lock_acquired = True

            call_command(
                "createcachetable",
                database=database_alias,
                verbosity=options["verbosity"],
            )
            if cache._table not in database.introspection.table_names():
                raise CommandError(
                    f"Cache table {cache._table!r} was not created."
                )
        except (DatabaseError, CommandError) as exc:
            raise CommandError(
                f"Could not provision cache table {cache._table!r} "
                f"using database {database_alias!r}: {exc}"
            ) from exc
        finally:
            if lock_acquired:
                with database.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(%s)",
                        [POSTGRES_CACHE_PROVISION_LOCK],
                    )
