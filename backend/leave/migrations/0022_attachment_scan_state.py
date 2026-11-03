from django.db import migrations, models


def migrate_legacy_scan_statuses(apps, schema_editor):
    attachment_model = apps.get_model("leave", "LeaveRequestAttachment")
    attachments = attachment_model.objects.using(schema_editor.connection.alias)
    attachments.filter(scan_status="unscanned").update(scan_status="pending_scan")
    attachments.filter(deletion_status="deleted").update(scan_status="deleted")


class Migration(migrations.Migration):

    dependencies = [
        ("leave", "0021_jobeffect_append_only"),
    ]

    operations = [
        migrations.AddField(
            model_name="leaverequestattachment",
            name="scan_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="leaverequestattachment",
            name="scan_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="leaverequestattachment",
            name="scanned_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="leaverequestattachment",
            name="scan_result_code",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.RunPython(migrate_legacy_scan_statuses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="leaverequestattachment",
            name="scan_status",
            field=models.CharField(
                choices=[
                    ("pending_scan", "Pending scan"),
                    ("scanning", "Scanning"),
                    ("clean", "Clean"),
                    ("rejected", "Rejected"),
                    ("scanner_unavailable", "Scanner unavailable"),
                    ("scan_failed", "Scan failed"),
                    ("deleted", "Deleted"),
                ],
                default="pending_scan",
                max_length=24,
            ),
        ),
    ]
