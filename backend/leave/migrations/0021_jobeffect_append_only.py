from django.db import migrations
from django.db.utils import NotSupportedError


def install_job_effect_guard(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor == "postgresql":
        schema_editor.execute("""
            CREATE TRIGGER leave_jobeffect_append_only
            BEFORE UPDATE OR DELETE ON leave_jobeffect
            FOR EACH ROW EXECUTE FUNCTION leave_reject_append_only_mutation()
        """)
    elif connection.vendor == "sqlite":
        schema_editor.execute("""
            CREATE TRIGGER leave_jobeffect_no_update
            BEFORE UPDATE ON leave_jobeffect
            BEGIN
                SELECT RAISE(ABORT, 'leave_jobeffect is append-only; UPDATE is prohibited');
            END
        """)
        schema_editor.execute("""
            CREATE TRIGGER leave_jobeffect_no_delete
            BEFORE DELETE ON leave_jobeffect
            BEGIN
                SELECT RAISE(ABORT, 'leave_jobeffect is append-only; DELETE is prohibited');
            END
        """)
    else:
        raise NotSupportedError(
            f"Append-only history guards are not implemented for {connection.vendor}."
        )


def remove_job_effect_guard(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor == "postgresql":
        schema_editor.execute(
            "DROP TRIGGER IF EXISTS leave_jobeffect_append_only ON leave_jobeffect"
        )
    elif connection.vendor == "sqlite":
        schema_editor.execute("DROP TRIGGER IF EXISTS leave_jobeffect_no_update")
        schema_editor.execute("DROP TRIGGER IF EXISTS leave_jobeffect_no_delete")
    else:
        raise NotSupportedError(
            f"Append-only history guards are not implemented for {connection.vendor}."
        )


class Migration(migrations.Migration):

    dependencies = [
        ("leave", "0020_notificationdelivery"),
    ]

    operations = [
        migrations.RunPython(install_job_effect_guard, remove_job_effect_guard),
    ]
