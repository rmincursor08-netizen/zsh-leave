import datetime as dt
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from leave.management.commands.sync_runtime_db_grants import APPEND_ONLY_TABLES

from leave.entitlement_services import create_entitlement
from leave.models import (
    AuditEvent,
    CalendarHolidayHistory,
    Company,
    EntitlementEvent,
    EmployeeProfile,
    JobEffect,
    LeaveEntitlement,
    LeavePolicyVersion,
    LeaveRequest,
    LeaveRequestComment,
    LeaveRequestEvent,
    LeaveType,
    PublicHolidayHistory,
)

User = get_user_model()

EXPECTED_APPEND_ONLY_TABLES = {
    AuditEvent._meta.db_table,
    CalendarHolidayHistory._meta.db_table,
    EntitlementEvent._meta.db_table,
    JobEffect._meta.db_table,
    LeavePolicyVersion._meta.db_table,
    LeaveRequestComment._meta.db_table,
    LeaveRequestEvent._meta.db_table,
    PublicHolidayHistory._meta.db_table,
}


class AppendOnlyIntegrityTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_user(username="history-actor")
        company = Company.objects.create(code="HISTORY", name="History company")
        employee = EmployeeProfile.objects.create(
            user=self.actor,
            company=company,
            personnel_code="HISTORY-1",
            start_date=dt.date(2020, 1, 1),
        )
        leave_type = LeaveType.objects.create(
            key="history-annual",
            label_fa="Annual",
            counts_against_balance=True,
        )
        request = LeaveRequest.objects.create(
            employee=employee,
            leave_type=leave_type,
            start_date=dt.date(2026, 11, 2),
            end_date=dt.date(2026, 11, 2),
            working_days="1",
            status="PENDING",
        )
        entitlement = create_entitlement(
            employee=employee,
            leave_type=leave_type,
            year=1405,
            base_days="10",
            actor=self.actor,
            reason="opening balance",
            idempotency_key="append-only-test-opening",
        )

        self.rows = [
            (AuditEvent, AuditEvent.objects.create(
                actor=self.actor, origin="test", action="created", entity="Test",
                entity_id="1", before=None, after={"state": "created"},
            ), "action"),
            (CalendarHolidayHistory, CalendarHolidayHistory.objects.create(
                holiday_id=1, action="create", before=None, after={"active": True},
                actor=self.actor,
            ), "action"),
            (LeaveRequestComment, LeaveRequestComment.objects.create(
                request=request, actor=self.actor, body="original comment",
            ), "body"),
            (LeaveRequestEvent, LeaveRequestEvent.objects.create(
                request=request, actor=self.actor, origin="employee", event="submit",
                from_status="", to_status="PENDING",
            ), "event"),
            (LeavePolicyVersion, LeavePolicyVersion.objects.create(
                scope="organization", version=1, effective_from=dt.date(2026, 1, 1),
                values={"policy": "v1"}, reason="initial policy", created_by=self.actor,
            ), "reason"),
            (PublicHolidayHistory, PublicHolidayHistory.objects.create(
                holiday_id=1, action="create", before=None, after={"label": "Holiday"},
                actor=self.actor,
            ), "action"),
            (EntitlementEvent, EntitlementEvent.objects.create(
                entitlement=entitlement, actor=self.actor, kind="opening",
                before={"base": "0"}, after={"base": "10"}, reason="opening balance",
            ), "reason"),
            (JobEffect, JobEffect.objects.create(
                job_name="append-only-test", effect_key="append-only-test-effect",
            ), "job_name"),
        ]

    def test_existing_model_save_and_delete_are_rejected(self):
        for model, row, field in self.rows:
            with self.subTest(model=model.__name__, operation="save"):
                setattr(row, field, "tampered")
                with self.assertRaises(PermissionError):
                    row.save()
            with self.subTest(model=model.__name__, operation="delete"):
                with self.assertRaises(PermissionError):
                    row.delete()

    def test_queryset_update_delete_and_bulk_update_are_rejected(self):
        for model, row, field in self.rows:
            original = getattr(row, field)
            with self.subTest(model=model.__name__, operation="update"):
                with self.assertRaises(DatabaseError):
                    with transaction.atomic():
                        model.objects.filter(pk=row.pk).update(**{field: "tampered"})
                self.assertEqual(getattr(model.objects.get(pk=row.pk), field), original)

            with self.subTest(model=model.__name__, operation="bulk_update"):
                row.refresh_from_db()
                setattr(row, field, "tampered")
                with self.assertRaises(DatabaseError):
                    with transaction.atomic():
                        model.objects.bulk_update([row], [field])
                self.assertEqual(getattr(model.objects.get(pk=row.pk), field), original)

            with self.subTest(model=model.__name__, operation="delete"):
                with self.assertRaises(DatabaseError):
                    with transaction.atomic():
                        model.objects.filter(pk=row.pk).delete()
                self.assertTrue(model.objects.filter(pk=row.pk).exists())

    def test_runtime_grant_policy_covers_every_append_only_model(self):
        self.assertSetEqual(APPEND_ONLY_TABLES, EXPECTED_APPEND_ONLY_TABLES)

    def test_direct_sql_update_and_delete_are_rejected(self):
        if connection.vendor not in {"postgresql", "sqlite"}:
            self.skipTest("Database append-only triggers are configured for PostgreSQL and SQLite.")

        for model, row, _field in self.rows:
            table = connection.ops.quote_name(model._meta.db_table)
            pk_column = connection.ops.quote_name(model._meta.pk.column)
            with self.subTest(model=model.__name__, operation="raw update"):
                with self.assertRaises(DatabaseError):
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute(
                                f"UPDATE {table} SET {pk_column} = {pk_column} WHERE {pk_column} = %s",
                                [row.pk],
                            )
                self.assertTrue(model.objects.filter(pk=row.pk).exists())

            with self.subTest(model=model.__name__, operation="raw delete"):
                with self.assertRaises(DatabaseError):
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute(
                                f"DELETE FROM {table} WHERE {pk_column} = %s",
                                [row.pk],
                            )
                self.assertTrue(model.objects.filter(pk=row.pk).exists())

    @skipUnless(connection.vendor == "postgresql", "Requires a PostgreSQL test database.")
    def test_postgresql_triggers_reject_direct_sql_mutations(self):
        for model, row, _field in self.rows:
            table = connection.ops.quote_name(model._meta.db_table)
            pk_column = connection.ops.quote_name(model._meta.pk.column)
            for statement in (
                f"UPDATE {table} SET {pk_column} = {pk_column} WHERE {pk_column} = %s",
                f"DELETE FROM {table} WHERE {pk_column} = %s",
            ):
                with self.subTest(model=model.__name__, statement=statement.split()[0]):
                    with self.assertRaises(DatabaseError):
                        with transaction.atomic():
                            with connection.cursor() as cursor:
                                cursor.execute(statement, [row.pk])
                    self.assertTrue(model.objects.filter(pk=row.pk).exists())

    def test_append_and_related_record_creation_remain_available(self):
        request = self.rows[2][1].request
        comment = LeaveRequestComment.objects.create(
            request=request,
            actor=self.actor,
            body="a second immutable comment",
        )
        event = LeaveRequestEvent.objects.create(
            request=request,
            actor=self.actor,
            origin="employee",
            event="comment",
            from_status="PENDING",
            to_status="PENDING",
        )
        audit = AuditEvent.objects.create(
            actor=self.actor,
            origin="test",
            action="comment.created",
            entity="LeaveRequestComment",
            entity_id=str(comment.pk),
            after={"event_id": event.pk},
        )

        self.assertEqual(comment.request_id, request.pk)
        self.assertEqual(event.request_id, request.pk)
        self.assertEqual(audit.entity_id, str(comment.pk))

    def test_rejected_mutation_rolls_back_without_poisoning_outer_transaction(self):
        audit = self.rows[0][1]
        with transaction.atomic():
            with self.assertRaises(DatabaseError):
                with transaction.atomic():
                    AuditEvent.objects.filter(pk=audit.pk).update(action="tampered")
            created = AuditEvent.objects.create(
                actor=self.actor,
                origin="test",
                action="after_rejected_mutation",
                entity="Test",
                entity_id="2",
            )
            audit.refresh_from_db()
            self.assertEqual(audit.action, "created")
            self.assertTrue(AuditEvent.objects.filter(pk=created.pk).exists())
