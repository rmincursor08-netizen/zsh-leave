from django.core.management.base import BaseCommand, CommandError

from leave.attachment_scanning import RETRYABLE_STATUSES, scan_stored_attachment
from leave.models import LeaveRequestAttachment


class Command(BaseCommand):
    help = "Retry pending or failed private attachment malware scans."

    def add_arguments(self, parser):
        parser.add_argument("--attachment-id", type=int)
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        limit = options["limit"]
        if not 1 <= limit <= 1000:
            raise CommandError("--limit must be between 1 and 1000.")
        rows = LeaveRequestAttachment.objects.filter(
            scan_status__in=RETRYABLE_STATUSES,
            deletion_status=LeaveRequestAttachment.DELETION_ACTIVE,
        ).order_by("created_at", "pk")
        if options["attachment_id"] is not None:
            rows = rows.filter(pk=options["attachment_id"])

        attachment_ids = list(rows.values_list("pk", flat=True)[:limit])
        for attachment_id in attachment_ids:
            scan_stored_attachment(attachment_id)
        self.stdout.write(f"Processed {len(attachment_ids)} attachment scan(s).")
