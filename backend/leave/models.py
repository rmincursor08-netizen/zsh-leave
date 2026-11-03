"""مدل‌های هسته. مهاجرت‌ها باید روی ماشین دارای Django تولید و بازبینی شوند (makemigrations)."""
import uuid

from django.conf import settings
from django.db import models, transaction
from django.db.models import Q

from .attachment_storage import PrivateAttachmentStorage
from zsh_core import workflow as wf

STATUS_CHOICES = [(s, s) for s in (wf.PENDING, wf.ESCALATED, wf.APPROVED, wf.REJECTED,
                                   wf.CANCELLED, wf.WITHDRAWAL_PENDING)]


class OrganizationalHierarchyQuerySet(models.QuerySet):
    relationship_fields = {
        "OrganizationSite": {"company", "company_id"},
        "Department": {"company", "company_id", "parent", "parent_id"},
        "EmployeeProfile": {
            "company", "company_id", "site", "site_id", "department", "department_id",
            "manager", "manager_id",
        },
    }

    def update(self, **kwargs):
        protected_fields = self.relationship_fields.get(self.model.__name__, set())
        if protected_fields.intersection(kwargs):
            from django.core.exceptions import ValidationError

            raise ValidationError("Use model save or the hierarchy service to change organizational relationships")
        return super().update(**kwargs)

    def bulk_create(self, objs, **kwargs):
        if kwargs.get("ignore_conflicts") or kwargs.get("update_conflicts"):
            raise ValueError("Conflict-ignoring bulk writes cannot validate organizational relationships")
        objects = list(objs)
        with transaction.atomic():
            for obj in objects:
                obj.save(force_insert=True)
        return objects

    def bulk_update(self, objs, fields, batch_size=None):
        protected_fields = self.relationship_fields.get(self.model.__name__, set())
        if protected_fields.intersection(fields):
            objects = list(objs)
            with transaction.atomic():
                for obj in objects:
                    obj.save(update_fields=fields)
            return len(objects)
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def delete(self):
        protected_fields = self.relationship_fields.get(self.model.__name__, set())
        if not protected_fields:
            return super().delete()
        objects = list(self)
        total_deleted = 0
        deleted_by_model = {}
        with transaction.atomic():
            for obj in objects:
                deleted, counts = obj.delete()
                total_deleted += deleted
                for model_name, count in counts.items():
                    deleted_by_model[model_name] = deleted_by_model.get(model_name, 0) + count
        return total_deleted, deleted_by_model


class OrganizationalHierarchyManager(models.Manager.from_queryset(OrganizationalHierarchyQuerySet)):
    pass


class Company(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)


class OrganizationSite(models.Model):
    objects = OrganizationalHierarchyManager()

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="sites")
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_site_code_in_company"),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.pk is not None:
            original_company_id = OrganizationSite.objects.filter(pk=self.pk).values_list(
                "company_id", flat=True,
            ).first()
            if original_company_id is not None and original_company_id != self.company_id:
                raise ValidationError({
                    "company": "مالکیت سایت پس از ایجاد قابل تغییر نیست؛ برای انتقال، سایت جدید بسازید",
                })

    def save(self, *args, **kwargs):
        from .hierarchy_services import lock_organizational_hierarchy

        with transaction.atomic():
            lock_organizational_hierarchy()
            self.full_clean()
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from .hierarchy_services import lock_organizational_hierarchy

        with transaction.atomic():
            lock_organizational_hierarchy()
            return super().delete(*args, **kwargs)


class Department(models.Model):
    objects = OrganizationalHierarchyManager()

    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=200)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_dept_code_in_company"),
            models.CheckConstraint(
                condition=Q(parent__isnull=True) | ~Q(parent=models.F("id")),
                name="department_parent_not_self",
            ),
        ]

    def clean(self):
        from .hierarchy_services import validate_department_relationships

        validate_department_relationships(self)

    def save(self, *args, **kwargs):
        from .hierarchy_services import lock_organizational_hierarchy

        with transaction.atomic():
            lock_organizational_hierarchy()
            self.full_clean()
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from .hierarchy_services import lock_organizational_hierarchy

        with transaction.atomic():
            lock_organizational_hierarchy()
            return super().delete(*args, **kwargs)


class EmployeeProfile(models.Model):
    objects = OrganizationalHierarchyManager()

    ACCOUNT_CREATED = "created"
    INVITATION_PENDING = "invitation_pending"
    INVITATION_SENT = "invitation_sent"
    INVITATION_EXPIRED = "invitation_expired"
    INVITATION_REVOKED = "invitation_revoked"
    ACCOUNT_ACTIVATED = "account_activated"
    ACCOUNT_SUSPENDED = "account_suspended"
    ACCOUNT_OFFBOARDED = "account_offboarded"
    ACCOUNT_STATUS_CHOICES = [
        (ACCOUNT_CREATED, "Employee record created"),
        (INVITATION_PENDING, "Invitation pending"),
        (INVITATION_SENT, "Invitation sent"),
        (INVITATION_EXPIRED, "Invitation expired"),
        (INVITATION_REVOKED, "Invitation revoked"),
        (ACCOUNT_ACTIVATED, "Account activated"),
        (ACCOUNT_SUSPENDED, "Account suspended"),
        (ACCOUNT_OFFBOARDED, "Account offboarded"),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    site = models.ForeignKey(OrganizationSite, null=True, blank=True, on_delete=models.PROTECT)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT)
    personnel_code = models.CharField(max_length=32)
    manager = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="direct_reports")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_employee = models.BooleanField(default=True)   # حساب سرویس/مهمان = False
    account_status = models.CharField(
        max_length=24, choices=ACCOUNT_STATUS_CHOICES, default=ACCOUNT_ACTIVATED,
    )
    email_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "personnel_code"], name="uniq_personnel_code"),
            models.CheckConstraint(condition=~Q(manager=models.F("id")), name="manager_not_self"),
        ]
        indexes = [
            models.Index(fields=["company", "site"], name="employee_company_site_idx"),
        ]

    def clean(self):
        from .hierarchy_services import validate_employee_relationships

        validate_employee_relationships(self)

    def save(self, *args, **kwargs):
        from .hierarchy_services import lock_organizational_hierarchy

        with transaction.atomic():
            lock_organizational_hierarchy()
            self.full_clean()
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from .hierarchy_services import lock_organizational_hierarchy

        with transaction.atomic():
            lock_organizational_hierarchy()
            reports = EmployeeProfile.objects.select_for_update().filter(manager_id=self.pk)
            for report in reports:
                report.manager = None
                report.save(update_fields=["manager"])
            return super().delete(*args, **kwargs)


class EmployeeInvitation(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SENT = "sent"
    STATUS_EXPIRED = "expired"
    STATUS_REVOKED = "revoked"
    STATUS_CONSUMED = "consumed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SENT, "Sent"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_REVOKED, "Revoked"),
        (STATUS_CONSUMED, "Consumed"),
    ]

    PURPOSE_EMPLOYEE_ACTIVATION = "employee_activation"

    employee = models.ForeignKey(
        EmployeeProfile, on_delete=models.PROTECT, related_name="invitations",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    purpose = models.CharField(
        max_length=32, default=PURPOSE_EMPLOYEE_ACTIVATION,
        choices=[(PURPOSE_EMPLOYEE_ACTIVATION, "Employee account activation")],
    )
    email = models.EmailField(max_length=254)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    expires_at = models.DateTimeField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="created_employee_invitations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["employee"],
                condition=Q(status__in=["pending", "sent"]),
                name="one_active_employee_invitation",
            ),
        ]
        indexes = [models.Index(fields=["employee", "status"], name="invite_emp_status_idx")]


class RoleAssignment(models.Model):
    ROLE_HR = "hr"
    ROLE_ADMIN = "admin"
    ROLE_EXEC = "exec"
    ROLE_CHOICES = [
        (ROLE_HR, "HR"),
        (ROLE_ADMIN, "System administrator"),
        (ROLE_EXEC, "Executive viewer"),
    ]

    SCOPE_GLOBAL = "global"
    SCOPE_COMPANY = "company"
    SCOPE_SITE = "site"
    SCOPE_DEPARTMENT = "department"
    SCOPE_EMPLOYEE = "employee"
    SCOPE_CHOICES = [
        (SCOPE_GLOBAL, "Global"),
        (SCOPE_COMPANY, "Company"),
        (SCOPE_SITE, "Site"),
        (SCOPE_DEPARTMENT, "Department"),
        (SCOPE_EMPLOYEE, "Employee"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="role_assignments")
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES)
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.PROTECT)
    site = models.ForeignKey(OrganizationSite, null=True, blank=True, on_delete=models.PROTECT)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT)
    employee = models.ForeignKey(EmployeeProfile, null=True, blank=True, on_delete=models.PROTECT)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.PROTECT,
                                   related_name="+")
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="+")
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(scope="global", company__isnull=True, site__isnull=True,
                      department__isnull=True, employee__isnull=True)
                    | Q(scope="company", company__isnull=False, site__isnull=True,
                        department__isnull=True, employee__isnull=True)
                    | Q(scope="site", company__isnull=False, site__isnull=False,
                        department__isnull=True, employee__isnull=True)
                    | Q(scope="department", company__isnull=False, site__isnull=True,
                        department__isnull=False, employee__isnull=True)
                    | Q(scope="employee", company__isnull=False, site__isnull=True,
                        department__isnull=True, employee__isnull=False)
                ),
                name="role_assignment_valid_scope",
            ),
            models.CheckConstraint(
                condition=(
                    Q(role="admin", scope="global")
                    | Q(role="hr", scope__in=["company", "site", "department", "employee"])
                    | Q(role="exec", scope__in=["company", "site", "department"])
                ),
                name="role_assignment_valid_role_scope",
            ),
            models.UniqueConstraint(
                fields=["user", "role"],
                condition=Q(is_active=True, scope="global"),
                name="uniq_active_global_role_assignment",
            ),
            models.UniqueConstraint(
                fields=["user", "role", "company"],
                condition=Q(is_active=True, scope="company"),
                name="uniq_active_company_role_assignment",
            ),
            models.UniqueConstraint(
                fields=["user", "role", "site"],
                condition=Q(is_active=True, scope="site"),
                name="uniq_active_site_role_assignment",
            ),
            models.UniqueConstraint(
                fields=["user", "role", "department"],
                condition=Q(is_active=True, scope="department"),
                name="uniq_active_department_role_assignment",
            ),
            models.UniqueConstraint(
                fields=["user", "role", "employee"],
                condition=Q(is_active=True, scope="employee"),
                name="uniq_active_employee_role_assignment",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "role", "is_active"], name="role_assign_user_act_idx"),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.role == self.ROLE_ADMIN and self.scope != self.SCOPE_GLOBAL:
            raise ValidationError({"scope": "نقش مدیر سامانه فقط می‌تواند سراسری باشد"})
        if self.role in {self.ROLE_HR, self.ROLE_EXEC} and self.scope == self.SCOPE_GLOBAL:
            raise ValidationError({"scope": "نقش HR و گزارش‌خوان باید محدوده سازمانی داشته باشد"})
        if self.scope == self.SCOPE_SITE and self.site_id and self.company_id != self.site.company_id:
            raise ValidationError({"site": "سایت باید متعلق به شرکت انتخاب‌شده باشد"})
        if (self.scope == self.SCOPE_DEPARTMENT and self.department_id
                and self.company_id != self.department.company_id):
            raise ValidationError({"department": "دپارتمان باید متعلق به شرکت انتخاب‌شده باشد"})
        if (self.scope == self.SCOPE_EMPLOYEE and self.employee_id
                and self.company_id != self.employee.company_id):
            raise ValidationError({"employee": "کارمند باید متعلق به شرکت انتخاب‌شده باشد"})
        if self.role == self.ROLE_EXEC and self.scope == self.SCOPE_EMPLOYEE:
            raise ValidationError({"scope": "گزارش‌خوان فقط می‌تواند محدوده تجمیعی داشته باشد"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class WorkPattern(models.Model):
    name = models.CharField(max_length=120)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="work_patterns")
    site = models.ForeignKey(OrganizationSite, null=True, blank=True, on_delete=models.PROTECT)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT)
    employee = models.ForeignKey(EmployeeProfile, null=True, blank=True, on_delete=models.PROTECT)
    working_weekdays = models.JSONField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    break_minutes = models.PositiveSmallIntegerField(default=0)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    shift_kind = models.CharField(max_length=16, default="fixed")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["company", "effective_from", "effective_to"]),
            models.Index(fields=["site", "effective_from", "effective_to"]),
            models.Index(fields=["department", "effective_from", "effective_to"]),
            models.Index(fields=["employee", "effective_from", "effective_to"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True) | Q(effective_from__lte=models.F("effective_to")),
                name="work_pattern_valid_dates",
            ),
            models.CheckConstraint(condition=Q(shift_kind="fixed"), name="fixed_work_pattern_only"),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        selectors = [self.site_id, self.department_id, self.employee_id]
        if sum(value is not None for value in selectors) > 1:
            raise ValidationError("الگوی کاری باید دقیقاً یک دامنه اختصاصی داشته باشد")
        if self.site_id and self.site.company_id != self.company_id:
            raise ValidationError({"site": "سایت باید متعلق به همان شرکت باشد"})
        if self.department_id and self.department.company_id != self.company_id:
            raise ValidationError({"department": "دپارتمان باید متعلق به همان شرکت باشد"})
        if self.employee_id and self.employee.company_id != self.company_id:
            raise ValidationError({"employee": "کارمند باید متعلق به همان شرکت باشد"})
        weekdays = self.working_weekdays
        if (not isinstance(weekdays, list) or not weekdays or
                any(isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6
                    for day in weekdays) or len(set(weekdays)) != len(weekdays)):
            raise ValidationError({"working_weekdays": "روزهای هفته باید فهرست یکتای اعداد ۰ تا ۶ باشد"})
        if self.start_time is None or self.end_time is None:
            raise ValidationError({"start_time": "ساعت شروع و پایان الزامی است"})
        if self.start_time >= self.end_time:
            raise ValidationError({"end_time": "ساعت پایان باید پس از شروع باشد"})
        duration = ((self.end_time.hour * 60 + self.end_time.minute) -
                    (self.start_time.hour * 60 + self.start_time.minute))
        if self.break_minutes >= duration:
            raise ValidationError({"break_minutes": "مدت استراحت باید کمتر از ساعات حضور باشد"})

    @property
    def daily_work_minutes(self) -> int:
        start = self.start_time.hour * 60 + self.start_time.minute
        end = self.end_time.hour * 60 + self.end_time.minute
        return end - start - self.break_minutes

    @property
    def weekly_work_minutes(self) -> int:
        return self.daily_work_minutes * len(self.working_weekdays)


class LeavePolicyVersion(models.Model):
    SCOPE_CHOICES = [
        ("organization", "organization"),
        ("company", "company"),
        ("site", "site"),
        ("department", "department"),
        ("employee", "employee"),
    ]

    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES)
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.PROTECT)
    site = models.ForeignKey(OrganizationSite, null=True, blank=True, on_delete=models.PROTECT)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT)
    employee = models.ForeignKey(EmployeeProfile, null=True, blank=True, on_delete=models.PROTECT)
    version = models.PositiveIntegerField()
    effective_from = models.DateField()
    values = models.JSONField()
    reason = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["scope", "effective_from"]),
            models.Index(fields=["company", "effective_from"]),
            models.Index(fields=["site", "effective_from"]),
            models.Index(fields=["department", "effective_from"]),
            models.Index(fields=["employee", "effective_from"]),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        expected = {
            "organization": (False, False, False, False),
            "company": (True, False, False, False),
            "site": (True, True, False, False),
            "department": (True, False, True, False),
            "employee": (True, False, False, True),
        }.get(self.scope)
        actual = (self.company_id is not None, self.site_id is not None,
                  self.department_id is not None, self.employee_id is not None)
        if expected is None or actual != expected:
            raise ValidationError("دامنه سیاست و محدوده سازمانی آن سازگار نیست")
        if self.site_id and self.site.company_id != self.company_id:
            raise ValidationError({"site": "سایت باید متعلق به همان شرکت باشد"})
        if self.department_id and self.department.company_id != self.company_id:
            raise ValidationError({"department": "دپارتمان باید متعلق به همان شرکت باشد"})
        if self.employee_id and self.employee.company_id != self.company_id:
            raise ValidationError({"employee": "کارمند باید متعلق به همان شرکت باشد"})
        if not self.reason.strip():
            raise ValidationError({"reason": "دلیل ثبت نسخه سیاست الزامی است"})

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("نسخه سیاست تغییرناپذیر است؛ نسخه جدید ثبت کنید")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("نسخه سیاست قابل حذف نیست")


class CalendarHoliday(models.Model):
    SCOPE_CHOICES = [
        ("national", "national"),
        ("organization", "organization"),
        ("company", "company"),
        ("site", "site"),
    ]

    title = models.CharField(max_length=200)
    start_date = models.DateField()
    end_date = models.DateField()
    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES)
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.PROTECT)
    site = models.ForeignKey(OrganizationSite, null=True, blank=True, on_delete=models.PROTECT)
    holiday_kind = models.CharField(max_length=32, default="official")
    description = models.TextField(blank=True)
    source = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT,
                                   related_name="+")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["start_date", "end_date", "is_active"]),
            models.Index(fields=["scope", "company", "site"]),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(start_date__lte=models.F("end_date")),
                                   name="calendar_holiday_valid_dates"),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        expected = {
            "national": (False, False),
            "organization": (False, False),
            "company": (True, False),
            "site": (True, True),
        }.get(self.scope)
        actual = (self.company_id is not None, self.site_id is not None)
        if expected is None or actual != expected:
            raise ValidationError("دامنه تعطیلی و محدوده سازمانی آن سازگار نیست")
        if self.site_id and self.site.company_id != self.company_id:
            raise ValidationError({"site": "سایت باید متعلق به همان شرکت باشد"})


class LeaveType(models.Model):
    key = models.CharField(max_length=32, unique=True)
    label_fa = models.CharField(max_length=128)
    label_en = models.CharField(max_length=128, blank=True)
    color = models.CharField(max_length=7, default="#3b6ea5")
    icon = models.CharField(max_length=16, blank=True)
    counts_against_balance = models.BooleanField(default=False)
    requires_approval = models.BooleanField(default=True)
    requires_note = models.BooleanField(default=False)
    requires_replacement = models.BooleanField(default=False)
    employee_requestable = models.BooleanField(default=True)
    confidential = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=Q(confidential=False) | Q(employee_requestable=False),
            name="confidential_not_self_requestable")]


class LeaveEntitlement(models.Model):
    employee = models.ForeignKey(EmployeeProfile, on_delete=models.PROTECT)
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT)
    year = models.IntegerField()                         # سال مرخصی طبق سیاست (شمسی/میلادی)
    base_days = models.DecimalField(max_digits=5, decimal_places=1)
    carry_over_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    manual_adjustment = models.DecimalField(max_digits=5, decimal_places=1, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "leave_type", "year"],
                                               name="uniq_entitlement")]

    def save(self, *args, **kwargs):
        fields = set(kwargs.get("update_fields") or (
            "base_days", "carry_over_days", "manual_adjustment",
        ))
        allocation_fields = {"base_days", "carry_over_days", "manual_adjustment"}
        if not getattr(self, "_ledger_write", False) and fields & allocation_fields:
            if self.pk is None:
                if any(getattr(self, field) != 0 for field in allocation_fields):
                    raise PermissionError("تخصیص سهمیه باید از طریق دفترکل ثبت شود")
            else:
                using = kwargs.get("using") or self._state.db
                previous = type(self).objects.using(using).filter(pk=self.pk).values(
                    *allocation_fields,
                ).first()
                if previous and any(
                    getattr(self, field) != previous[field] for field in allocation_fields
                ):
                    raise PermissionError("تغییر سهمیه باید از طریق دفترکل ثبت شود")
        super().save(*args, **kwargs)


class EntitlementEvent(models.Model):
    """Append-only entitlement journal; allocation fields are the allocation projection."""
    entitlement = models.ForeignKey(LeaveEntitlement, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    kind = models.CharField(max_length=32)            # adjust | rollover | import | set
    before = models.JSONField()
    after = models.JSONField()
    reason = models.TextField()
    idempotency_key = models.CharField(max_length=128, unique=True, null=True, blank=True)
    base_delta = models.DecimalField(max_digits=8, decimal_places=1, default=0)
    carry_over_delta = models.DecimalField(max_digits=8, decimal_places=1, default=0)
    manual_adjustment_delta = models.DecimalField(max_digits=8, decimal_places=1, default=0)
    reserved_delta = models.DecimalField(max_digits=8, decimal_places=1, default=0)
    used_delta = models.DecimalField(max_digits=8, decimal_places=1, default=0)
    request = models.ForeignKey(
        "LeaveRequest", null=True, blank=True, on_delete=models.PROTECT,
        related_name="entitlement_events",
    )
    reversal_of = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT,
        related_name="reversal",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["entitlement", "created_at"], name="ent_event_ent_created_idx"),
            models.Index(fields=["request", "kind"], name="ent_event_req_kind_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("رکورد دفترکل قابل ویرایش نیست")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("رکورد دفترکل قابل حذف نیست")


class LeaveRequest(models.Model):
    employee = models.ForeignKey(EmployeeProfile, on_delete=models.PROTECT, related_name="requests")
    manager_snapshot = models.ForeignKey(EmployeeProfile, null=True, blank=True,
                                         on_delete=models.PROTECT, related_name="+")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT)
    policy_version = models.ForeignKey(LeavePolicyVersion, null=True, blank=True,
                                       on_delete=models.PROTECT, related_name="leave_requests")
    start_date = models.DateField()
    end_date = models.DateField()
    working_days = models.DecimalField(max_digits=5, decimal_places=1)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, db_index=True)
    reason = models.TextField(blank=True)
    replacement = models.ForeignKey(EmployeeProfile, null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="amendments")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["employee", "status"]),
            models.Index(fields=["manager_snapshot", "status"]),
            models.Index(fields=["start_date", "end_date"]),
            models.Index(fields=["status", "created_at"]),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(start_date__lte=models.F("end_date")), name="start_le_end"),
            models.CheckConstraint(condition=Q(working_days__gt=0), name="working_days_positive"),
            models.CheckConstraint(condition=Q(status__in=[c[0] for c in STATUS_CHOICES]),
                                   name="valid_status"),
            models.CheckConstraint(condition=~Q(replacement=models.F("employee")),
                                   name="replacement_not_self"),
            # فقط یک اصلاحیه غیرپایانی برای هر درخواست اصلی
            models.UniqueConstraint(
                fields=["supersedes"], name="one_active_amendment",
                condition=Q(supersedes__isnull=False) & ~Q(status__in=[wf.REJECTED, wf.CANCELLED])),
        ]


class _AppendOnly(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("رکورد حسابرسی/تاریخچه قابل ویرایش نیست")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("رکورد حسابرسی/تاریخچه قابل حذف نیست")


class CalendarHolidayHistory(_AppendOnly):
    holiday_id = models.BigIntegerField()
    action = models.CharField(max_length=16)
    before = models.JSONField(null=True)
    after = models.JSONField(null=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class LeaveRequestEvent(_AppendOnly):
    request = models.ForeignKey(LeaveRequest, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    origin = models.CharField(max_length=16)          # employee | manager | hr | system
    event = models.CharField(max_length=32)
    from_status = models.CharField(max_length=24, blank=True)
    to_status = models.CharField(max_length=24)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class LeaveRequestComment(_AppendOnly):
    request = models.ForeignKey(LeaveRequest, on_delete=models.PROTECT, related_name="comments")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


def attachment_upload_path(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in {"pdf", "png", "jpg", "jpeg"}:
        raise ValueError("Unsupported attachment extension.")
    return f"leave-attachments/{instance.request_id}/{uuid.uuid4().hex}.{extension}"


class LeaveRequestAttachment(models.Model):
    SCAN_PENDING = "pending_scan"
    SCAN_SCANNING = "scanning"
    SCAN_CLEAN = "clean"
    SCAN_REJECTED = "rejected"
    SCAN_UNAVAILABLE = "scanner_unavailable"
    SCAN_FAILED = "scan_failed"
    SCAN_DELETED = "deleted"
    SCAN_STATUS_CHOICES = [
        (SCAN_PENDING, "Pending scan"),
        (SCAN_SCANNING, "Scanning"),
        (SCAN_CLEAN, "Clean"),
        (SCAN_REJECTED, "Rejected"),
        (SCAN_UNAVAILABLE, "Scanner unavailable"),
        (SCAN_FAILED, "Scan failed"),
        (SCAN_DELETED, "Deleted"),
    ]
    DELETION_ACTIVE = "active"
    DELETION_PENDING = "pending"
    DELETION_DELETED = "deleted"
    DELETION_STATUS_CHOICES = [
        (DELETION_ACTIVE, "Active"),
        (DELETION_PENDING, "Deletion pending"),
        (DELETION_DELETED, "Deleted"),
    ]

    request = models.ForeignKey(LeaveRequest, on_delete=models.PROTECT, related_name="attachments")
    file = models.FileField(
        upload_to=attachment_upload_path,
        storage=PrivateAttachmentStorage(),
    )
    file_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    file_size = models.PositiveBigIntegerField()
    scan_status = models.CharField(
        max_length=24, choices=SCAN_STATUS_CHOICES, default=SCAN_PENDING,
    )
    scan_attempts = models.PositiveIntegerField(default=0)
    scan_started_at = models.DateTimeField(null=True, blank=True)
    scanned_at = models.DateTimeField(null=True, blank=True)
    scan_result_code = models.CharField(max_length=32, blank=True)
    deletion_status = models.CharField(
        max_length=16, choices=DELETION_STATUS_CHOICES, default=DELETION_ACTIVE,
    )
    legal_hold = models.BooleanField(default=False)
    deletion_requested_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name="deleted_attachments",
    )
    deletion_reason = models.CharField(max_length=500, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]


class NotificationRead(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    event = models.ForeignKey(LeaveRequestEvent, on_delete=models.CASCADE)
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "event"], name="uniq_notification_read"),
        ]


class NotificationDelivery(models.Model):
    SOURCE_REQUEST_EVENT = "request_event"
    SOURCE_ENTITLEMENT_EVENT = "entitlement_event"
    SOURCE_ACTIVATION = "activation"
    SOURCE_CHOICES = [
        (SOURCE_REQUEST_EVENT, "Request event"),
        (SOURCE_ENTITLEMENT_EVENT, "Entitlement event"),
        (SOURCE_ACTIVATION, "Account activation"),
    ]
    CHANNEL_EMAIL = "email"
    CHANNEL_CHOICES = [(CHANNEL_EMAIL, "Email")]
    STATUS_QUEUED = "queued"
    STATUS_SENDING = "sending"
    STATUS_SENT = "sent"
    STATUS_DELIVERED = "delivered"
    STATUS_FAILED = "failed"
    STATUS_UNAVAILABLE = "unavailable"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_SENDING, "Sending"),
        (STATUS_SENT, "Accepted by provider"),
        (STATUS_DELIVERED, "Delivered"),
        (STATUS_FAILED, "Failed"),
        (STATUS_UNAVAILABLE, "Unavailable"),
    ]

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    source_type = models.CharField(max_length=32, choices=SOURCE_CHOICES)
    request_event = models.ForeignKey(
        LeaveRequestEvent, null=True, blank=True, on_delete=models.PROTECT,
    )
    entitlement_event = models.ForeignKey(
        EntitlementEvent, null=True, blank=True, on_delete=models.PROTECT,
    )
    invitation = models.ForeignKey(
        EmployeeInvitation, null=True, blank=True, on_delete=models.PROTECT,
    )
    channel = models.CharField(max_length=16, choices=CHANNEL_CHOICES, default=CHANNEL_EMAIL)
    idempotency_key = models.CharField(max_length=160, unique=True)
    provider = models.CharField(max_length=64, default="django_email")
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_QUEUED, db_index=True,
    )
    attempt_count = models.PositiveSmallIntegerField(default=0)
    retry_after = models.DateTimeField(null=True, blank=True, db_index=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        source_type="request_event",
                        request_event__isnull=False,
                        entitlement_event__isnull=True,
                        invitation__isnull=True,
                    )
                    | Q(
                        source_type="entitlement_event",
                        request_event__isnull=True,
                        entitlement_event__isnull=False,
                        invitation__isnull=True,
                    )
                    | Q(
                        source_type="activation",
                        request_event__isnull=True,
                        entitlement_event__isnull=True,
                        invitation__isnull=False,
                    )
                ),
                name="notification_delivery_source_matches",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "retry_after", "created_at"], name="notif_delivery_due_idx"),
        ]


class BradfordNote(models.Model):
    employee = models.OneToOneField(EmployeeProfile, on_delete=models.CASCADE)
    body = models.TextField()
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)


class ContractPeriod(models.Model):
    employee = models.ForeignKey(EmployeeProfile, on_delete=models.PROTECT, related_name="contracts")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    work_days = models.JSONField()
    contract_percent = models.DecimalField(max_digits=5, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(start_date__lte=models.F("end_date")),
                name="contract_period_valid_dates",
            ),
            models.CheckConstraint(
                condition=Q(contract_percent__gt=0) & Q(contract_percent__lte=100),
                name="contract_period_percent_range",
            ),
        ]


class WorkflowIdempotency(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    key = models.CharField(max_length=128)
    operation = models.CharField(max_length=64)
    fingerprint = models.CharField(max_length=64)
    resource_type = models.CharField(max_length=64)
    resource_id = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["actor", "key"], name="uniq_workflow_idempotency"),
        ]


class AuditEvent(_AppendOnly):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    origin = models.CharField(max_length=16)
    action = models.CharField(max_length=64)
    entity = models.CharField(max_length=64)
    entity_id = models.CharField(max_length=64)
    before = models.JSONField(null=True)              # بدون متن آزاد/اطلاعات پزشکی
    after = models.JSONField(null=True)
    reason = models.TextField(blank=True)
    result = models.CharField(max_length=16, default="ok")
    correlation_id = models.CharField(max_length=128, blank=True)
    source = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SystemSetting(models.Model):
    """تنظیمات قابل ویرایش از داشبورد مدیریت؛ مقدار فقط با zsh_core.settings_schema اعتبارسنجی و ذخیره می‌شود."""
    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField(null=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)


class JobExecution(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_PARTIAL_FAILURE = "partial_failure"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_PARTIAL_FAILURE, "Partial failure"),
        (STATUS_FAILED, "Failed"),
    ]

    job_name = models.CharField(max_length=64)
    scheduled_for = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    retry_after = models.DateTimeField(null=True, blank=True)
    result_summary = models.JSONField(default=dict)
    error_code = models.CharField(max_length=64, blank=True)
    correlation_id = models.CharField(max_length=128, default=uuid.uuid4)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job_name", "scheduled_for"], name="uniq_job_execution_schedule",
            ),
        ]
        indexes = [
            models.Index(fields=["job_name", "-scheduled_for"], name="job_exec_name_sched_idx"),
            models.Index(fields=["status", "retry_after"], name="job_exec_status_retry_idx"),
        ]


class JobEffect(_AppendOnly):
    job_name = models.CharField(max_length=64)
    effect_key = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SchedulerHeartbeat(models.Model):
    name = models.CharField(max_length=64, unique=True)
    checked_at = models.DateTimeField()


class PublicHoliday(models.Model):
    """تعطیلی ثبت‌شده دستی (شمسی). jalali_year خالی = سالانه."""
    scope = models.CharField(max_length=64, default="*")      # '*' ملی؛ یا کد شرکت/منطقه
    jalali_year = models.IntegerField(null=True, blank=True)
    jalali_month = models.PositiveSmallIntegerField()
    jalali_day = models.PositiveSmallIntegerField()
    label = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT,
                                   related_name="+")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["scope", "jalali_year", "jalali_month", "jalali_day"],
                                    name="uniq_holiday_exact", nulls_distinct=False),
            models.CheckConstraint(condition=Q(jalali_month__gte=1, jalali_month__lte=12,
                                               jalali_day__gte=1, jalali_day__lte=31),
                                   name="holiday_month_day_range"),
        ]


class PublicHolidayHistory(_AppendOnly):
    holiday_id = models.BigIntegerField()          # بدون FK تا با حذف تعطیلی تاریخچه بماند
    action = models.CharField(max_length=16)       # create | update | delete
    before = models.JSONField(null=True)
    after = models.JSONField(null=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
