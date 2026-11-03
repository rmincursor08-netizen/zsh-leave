import { AUTO_CANCELLED, decideAutoCancel, snapshotAutoCancelDays } from "../lib/autoCancel";
import { BRADFORD_LEVEL_LABEL } from "../lib/bradford";
import { analyzeEmployee, bradfordConfigFromSettings, levelCounts } from "../lib/bradfordAnalysis";
import { clientPolicyFromSettings } from "../lib/clientPolicy";
import {
  findDailyConflict, findHourlyConflict, minutesBetween, validateHourlyDraft, workingDaysFromMinutes,
} from "../lib/hourlyLeave";
import { jalaliNow, todayIso, toGregorian, fromGregorian, isoToG, gToIso, addDaysIso } from "../lib/jalali";
import { KIND_CATEGORY, inferKind, type NotifKind } from "../lib/notificationPrefs";
import { estimateWorkingDays } from "../lib/workdays";
import type { Role } from "../lib/roles";
import { ApiError } from "./errors";
import type { Api, LeaveDraft, RequestFilters } from "./contract";
import type {
  Attachment, AuditRow, Balance, BradfordNote, CalendarEvent, CalendarHolidayEvent, Comment, Company, ContractPeriod,
  CoverageRow, Department, Employee, Entitlement, Holiday, ImportPreview, JobRow,
  LeaveRequest, LeaveType, NotificationItem, SettingItem, User,
} from "./types";

const WORK = [5, 6, 0, 1, 2];
const KEY = "zsh-leave-demo-v1";

type Store = {
  users: User[];
  sessionUserId: number | null;
  types: LeaveType[];
  requests: LeaveRequest[];
  comments: Record<number, Comment[]>;
  attachments: Record<number, Attachment[]>;
  entitlements: Entitlement[];
  holidays: Holiday[];
  notifications: NotificationItem[];
  employees: Employee[];
  companies: Company[];
  departments: Department[];
  contracts: ContractPeriod[];
  settings: SettingItem[];
  jobs: JobRow[];
  audit: AuditRow[];
  bradfordNotes: BradfordNote[];
  seq: number;
};

function fail(status: number, message_fa: string, code = "error"): never {
  throw new ApiError(status, { code, message_fa });
}

const jy = jalaliNow().jy;
const today = todayIso();

function seed(): Store {
  const companies: Company[] = [
    { id: 1, code: "HOLD", name: "هلدینگ زکریا شیمی (Demo)", isActive: true },
    { id: 2, code: "PROD", name: "شرکت تولیدی نمونه (Demo)", isActive: true },
  ];
  const departments: Department[] = [
    { id: 1, companyId: 1, code: "HR", name: "منابع انسانی", parentId: null },
    { id: 2, companyId: 1, code: "FIN", name: "مالی", parentId: null },
    { id: 3, companyId: 2, code: "PLANT", name: "تولید", parentId: null },
  ];
  const employees: Employee[] = [
    { id: 1, userId: 1, displayName: "سارا محمدی", personnelCode: "1001", companyId: 1, companyName: companies[0].name, departmentName: "مالی", positionName: "کارشناس", managerName: "رضا کریمی", startDate: "2020-09-22", endDate: null, isActive: true },
    { id: 2, userId: 2, displayName: "رضا کریمی", personnelCode: "1002", companyId: 1, companyName: companies[0].name, departmentName: "مالی", positionName: "مدیر واحد", managerName: null, startDate: "2018-03-21", endDate: null, isActive: true },
    { id: 3, userId: 3, displayName: "نرگس احمدی", personnelCode: "1003", companyId: 1, companyName: companies[0].name, departmentName: "منابع انسانی", positionName: "کارشناس HR", managerName: null, startDate: "2019-03-21", endDate: null, isActive: true },
    { id: 4, userId: 4, displayName: "مدیر سامانه", personnelCode: "9001", companyId: 1, companyName: companies[0].name, departmentName: "فناوری", positionName: "مدیر سامانه", managerName: null, startDate: "2017-03-21", endDate: null, isActive: true },
    { id: 5, userId: 5, displayName: "گزارش‌خوان ارشد", personnelCode: "8001", companyId: 1, companyName: companies[0].name, departmentName: "هلدینگ", positionName: "گزارش‌خوان", managerName: null, startDate: "2021-03-21", endDate: null, isActive: true },
    { id: 6, userId: 6, displayName: "علی رضایی", personnelCode: "1004", companyId: 2, companyName: companies[1].name, departmentName: "تولید", positionName: "سرپرست", managerName: "رضا کریمی", startDate: "2022-03-21", endDate: null, isActive: true },
  ];
  const users: User[] = [
    { id: 1, username: "sara", displayName: "سارا محمدی", email: "sara@demo.local", roles: ["employee"], employeeId: 1, companyId: 1, companyName: companies[0].name, departmentName: "مالی", personnelCode: "1001", managerName: "رضا کریمی", isEmployee: true },
    { id: 2, username: "reza", displayName: "رضا کریمی", email: "reza@demo.local", roles: ["employee", "manager"], employeeId: 2, companyId: 1, companyName: companies[0].name, departmentName: "مالی", personnelCode: "1002", managerName: null, isEmployee: true },
    { id: 3, username: "narges", displayName: "نرگس احمدی", email: "narges@demo.local", roles: ["employee", "hr"], employeeId: 3, companyId: 1, companyName: companies[0].name, departmentName: "منابع انسانی", personnelCode: "1003", managerName: null, isEmployee: true },
    { id: 4, username: "admin", displayName: "مدیر سامانه", email: "admin@demo.local", roles: ["admin"], employeeId: 4, companyId: 1, companyName: companies[0].name, departmentName: "فناوری", personnelCode: "9001", managerName: null, isEmployee: false },
    { id: 5, username: "exec", displayName: "گزارش‌خوان ارشد", email: "exec@demo.local", roles: ["exec"], employeeId: 5, companyId: 1, companyName: companies[0].name, departmentName: "هلدینگ", personnelCode: "8001", managerName: null, isEmployee: false },
  ];
  const types: LeaveType[] = [
    { id: 1, key: "annual", labelFa: "استحقاقی", color: "#0f4c5c", countsAgainstBalance: true, requiresApproval: true, requiresNote: false, requiresReplacement: false, employeeRequestable: true, confidential: false, enabled: true, sortOrder: 1 },
    { id: 2, key: "sick", labelFa: "استعلاجی", color: "#9a5b12", countsAgainstBalance: true, requiresApproval: true, requiresNote: true, requiresReplacement: false, employeeRequestable: true, confidential: false, enabled: true, sortOrder: 2 },
    { id: 3, key: "unpaid", labelFa: "بدون حقوق", color: "#4d585a", countsAgainstBalance: false, requiresApproval: true, requiresNote: true, requiresReplacement: true, employeeRequestable: true, confidential: false, enabled: true, sortOrder: 3 },
    { id: 4, key: "marriage", labelFa: "ازدواج (قالب)", color: "#215f86", countsAgainstBalance: false, requiresApproval: true, requiresNote: true, requiresReplacement: false, employeeRequestable: false, confidential: false, enabled: false, sortOrder: 4 },
    { id: 5, key: "confidential", labelFa: "محرمانه HR", color: "#6b3a6b", countsAgainstBalance: false, requiresApproval: false, requiresNote: true, requiresReplacement: false, employeeRequestable: false, confidential: true, enabled: true, sortOrder: 9 },
  ];
  const requests: LeaveRequest[] = [
    { id: 1, employeeId: 1, employeeName: "سارا محمدی", departmentName: "مالی", status: "PENDING", start: addDaysIso(today, 3), end: addDaysIso(today, 5), workingDays: "3.0", type: "annual", typeLabel: "استحقاقی", reason: "سفر خانوادگی (Demo)", canDecide: true, confidential: false, createdAt: today, updatedAt: today, replacementId: 6, replacementName: "علی رضایی", durationKind: "daily", autoCancelPolicyDays: 3, expiresAt: addDaysIso(today, 3) },
    { id: 2, employeeId: 1, employeeName: "سارا محمدی", departmentName: "مالی", status: "APPROVED", start: addDaysIso(today, -10), end: addDaysIso(today, -8), workingDays: "3.0", type: "annual", typeLabel: "استحقاقی", reason: "مرخصی قبلی (Demo)", canDecide: false, createdAt: addDaysIso(today, -20), updatedAt: addDaysIso(today, -18), durationKind: "daily" },
    { id: 3, employeeId: 6, employeeName: "علی رضایی", departmentName: "تولید", status: "ESCALATED", start: addDaysIso(today, 8), end: addDaysIso(today, 9), workingDays: "2.0", type: "unpaid", typeLabel: "بدون حقوق", reason: "کار شخصی (Demo)", canDecide: true, createdAt: today, updatedAt: today, durationKind: "daily", autoCancelPolicyDays: 3, expiresAt: addDaysIso(today, 3) },
    { id: 4, employeeId: 2, employeeName: "رضا کریمی", departmentName: "مالی", status: "APPROVED", start: addDaysIso(today, 12), end: addDaysIso(today, 12), workingDays: "1.0", type: "annual", typeLabel: "استحقاقی", reason: "", canDecide: false, createdAt: today, updatedAt: today, durationKind: "daily" },
    { id: 5, employeeId: 1, employeeName: "سارا محمدی", departmentName: "مالی", status: "PENDING", start: addDaysIso(today, 6), end: addDaysIso(today, 6), workingDays: "1.0", type: "annual", typeLabel: "استحقاقی", reason: "درخواست کهنه برای نمایش لغو خودکار (Demo)", canDecide: true, confidential: false, createdAt: addDaysIso(today, -5), updatedAt: addDaysIso(today, -5), durationKind: "daily", autoCancelPolicyDays: 3, expiresAt: addDaysIso(today, -2) },
    { id: 6, employeeId: 1, employeeName: "سارا محمدی", departmentName: "مالی", status: "APPROVED", start: today, end: today, workingDays: "0.19", type: "annual", typeLabel: "استحقاقی", reason: "مراجعه اداری (Demo)", canDecide: false, confidential: false, createdAt: addDaysIso(today, -1), updatedAt: addDaysIso(today, -1), durationKind: "hourly", startTime: "10:30", endTime: "12:00", durationMinutes: 90 },
    { id: 7, employeeId: 1, employeeName: "سارا محمدی", departmentName: "مالی", status: "APPROVED", start: addDaysIso(today, -12), end: addDaysIso(today, -12), workingDays: "1.0", type: "sick", typeLabel: "استعلاجی", reason: "دوره کوتاه (Demo)", canDecide: false, createdAt: addDaysIso(today, -13), updatedAt: addDaysIso(today, -12), durationKind: "daily" },
    { id: 8, employeeId: 1, employeeName: "سارا محمدی", departmentName: "مالی", status: "APPROVED", start: addDaysIso(today, -28), end: addDaysIso(today, -28), workingDays: "1.0", type: "sick", typeLabel: "استعلاجی", reason: "دوره کوتاه (Demo)", canDecide: false, createdAt: addDaysIso(today, -29), updatedAt: addDaysIso(today, -28), durationKind: "daily" },
    { id: 9, employeeId: 1, employeeName: "سارا محمدی", departmentName: "مالی", status: "APPROVED", start: addDaysIso(today, -45), end: addDaysIso(today, -44), workingDays: "2.0", type: "sick", typeLabel: "استعلاجی", reason: "دوره دو روزه (Demo)", canDecide: false, createdAt: addDaysIso(today, -46), updatedAt: addDaysIso(today, -44), durationKind: "daily" },
    { id: 10, employeeId: 6, employeeName: "علی رضایی", departmentName: "تولید", status: "APPROVED", start: addDaysIso(today, -3), end: addDaysIso(today, -3), workingDays: "1.0", type: "sick", typeLabel: "استعلاجی", reason: "کوتاه (Demo)", canDecide: false, createdAt: addDaysIso(today, -4), updatedAt: addDaysIso(today, -3), durationKind: "daily" },
    { id: 11, employeeId: 6, employeeName: "علی رضایی", departmentName: "تولید", status: "APPROVED", start: addDaysIso(today, -8), end: addDaysIso(today, -8), workingDays: "1.0", type: "sick", typeLabel: "استعلاجی", reason: "کوتاه (Demo)", canDecide: false, createdAt: addDaysIso(today, -9), updatedAt: addDaysIso(today, -8), durationKind: "daily" },
    { id: 12, employeeId: 6, employeeName: "علی رضایی", departmentName: "تولید", status: "APPROVED", start: addDaysIso(today, -15), end: addDaysIso(today, -15), workingDays: "1.0", type: "sick", typeLabel: "استعلاجی", reason: "کوتاه (Demo)", canDecide: false, createdAt: addDaysIso(today, -16), updatedAt: addDaysIso(today, -15), durationKind: "daily" },
    { id: 13, employeeId: 6, employeeName: "علی رضایی", departmentName: "تولید", status: "APPROVED", start: addDaysIso(today, -22), end: addDaysIso(today, -22), workingDays: "1.0", type: "sick", typeLabel: "استعلاجی", reason: "کوتاه (Demo)", canDecide: false, createdAt: addDaysIso(today, -23), updatedAt: addDaysIso(today, -22), durationKind: "daily" },
    { id: 14, employeeId: 6, employeeName: "علی رضایی", departmentName: "تولید", status: "APPROVED", start: addDaysIso(today, -33), end: addDaysIso(today, -33), workingDays: "1.0", type: "sick", typeLabel: "استعلاجی", reason: "کوتاه (Demo)", canDecide: false, createdAt: addDaysIso(today, -34), updatedAt: addDaysIso(today, -33), durationKind: "daily" },
    { id: 15, employeeId: 6, employeeName: "علی رضایی", departmentName: "تولید", status: "APPROVED", start: addDaysIso(today, -41), end: addDaysIso(today, -41), workingDays: "1.0", type: "sick", typeLabel: "استعلاجی", reason: "کوتاه (Demo)", canDecide: false, createdAt: addDaysIso(today, -42), updatedAt: addDaysIso(today, -41), durationKind: "daily" },
    { id: 16, employeeId: 6, employeeName: "علی رضایی", departmentName: "تولید", status: "APPROVED", start: addDaysIso(today, -55), end: addDaysIso(today, -55), workingDays: "1.0", type: "sick", typeLabel: "استعلاجی", reason: "کوتاه (Demo)", canDecide: false, createdAt: addDaysIso(today, -56), updatedAt: addDaysIso(today, -55), durationKind: "daily" },
    { id: 17, employeeId: 6, employeeName: "علی رضایی", departmentName: "تولید", status: "APPROVED", start: addDaysIso(today, -70), end: addDaysIso(today, -70), workingDays: "1.0", type: "sick", typeLabel: "استعلاجی", reason: "کوتاه (Demo)", canDecide: false, createdAt: addDaysIso(today, -71), updatedAt: addDaysIso(today, -70), durationKind: "daily" },
  ];
  const entitlements: Entitlement[] = employees.flatMap((e) => ([
    { id: e.id * 10 + 1, employeeId: e.id, employeeName: e.displayName, leaveTypeKey: "annual", leaveTypeLabel: "استحقاقی", year: jy, base: "30.0", carryOver: "2.0", adjustment: "0.0" },
    { id: e.id * 10 + 2, employeeId: e.id, employeeName: e.displayName, leaveTypeKey: "sick", leaveTypeLabel: "استعلاجی", year: jy, base: "10.0", carryOver: "0.0", adjustment: "0.0" },
  ]));
  const holidays: Holiday[] = [
    { id: 1, scope: "*", jalaliYear: null, jalaliMonth: 1, jalaliDay: 1, label: "نوروز (Demo)" },
    { id: 2, scope: "*", jalaliYear: jy, jalaliMonth: 1, jalaliDay: 13, label: "سیزده‌بدر (Demo)" },
    { id: 3, scope: "*", jalaliYear: null, jalaliMonth: 2, jalaliDay: 11, label: "روز کارگر (Demo)" },
  ];
  const settings: SettingItem[] = [
    { key: "policy.leave_calendar", value: "jalali", default: "jalali", labelFa: "تقویم سال مرخصی", group: "policy" },
    { key: "policy.annual_days_per_month", value: "2.5", default: "2.5", labelFa: "روز استحقاقی به‌ازای هر ماه خدمت", group: "policy" },
    { key: "policy.sick_days_per_year", value: "10", default: "10", labelFa: "روز استعلاجی در سال", group: "policy" },
    { key: "policy.sick_proration", value: "months", default: "months", labelFa: "تناسب استعلاجی با ماه‌های خدمت", group: "policy" },
    { key: "policy.partial_month_mode", value: "prorate", default: "prorate", labelFa: "محاسبه ماه ناقص", group: "policy" },
    { key: "policy.leave_day_counting", value: "working_days_only", default: "working_days_only", labelFa: "شمارش روز مرخصی", group: "policy" },
    { key: "policy.allow_use_before_accrual", value: true, default: true, labelFa: "استفاده پیش از تحقق ماهانه", group: "policy" },
    { key: "privacy.employee_sees_own_confidential_type", value: false, default: false, labelFa: "کارمند نوع محرمانه خود را ببیند", group: "privacy" },
    { key: "privacy.peers_see_leave_type", value: false, default: false, labelFa: "همکاران نوع را در تقویم ببینند", group: "privacy" },
    { key: "privacy.min_group_size", value: 5, default: 5, labelFa: "حداقل اندازه گروه در گزارش تجمیعی", group: "privacy" },
    { key: "workflow.reminder_after_working_days", value: null, default: null, labelFa: "یادآوری پس از N روز کاری", group: "workflow" },
    { key: "workflow.escalate_after_working_days", value: null, default: null, labelFa: "ارجاع خودکار پس از N روز کاری", group: "workflow" },
    { key: "workflow.auto_cancel_enabled", value: true, default: true, labelFa: "فعال بودن لغو خودکار درخواست‌های تأییدنشده", group: "workflow" },
    { key: "workflow.auto_cancel_pending_after_days", value: 3, default: 3, labelFa: "حداکثر مدت انتظار برای تأیید درخواست مرخصی (روز تقویمی)", group: "workflow" },
    { key: "policy.hourly_leave_enabled", value: true, default: true, labelFa: "فعال بودن مرخصی ساعتی", group: "policy" },
    { key: "policy.workday_start", value: "08:00", default: "08:00", labelFa: "شروع ساعات کاری", group: "policy" },
    { key: "policy.workday_end", value: "16:00", default: "16:00", labelFa: "پایان ساعات کاری", group: "policy" },
    { key: "policy.workday_minutes", value: 480, default: 480, labelFa: "دقایق روز کاری برای تبدیل مرخصی ساعتی", group: "policy" },
    { key: "policy.hourly_allow_outside_work_hours", value: false, default: false, labelFa: "اجازه مرخصی ساعتی خارج از ساعات کاری", group: "policy" },
    { key: "policy.hourly_allow_on_holiday", value: false, default: false, labelFa: "اجازه مرخصی ساعتی در تعطیل رسمی", group: "policy" },
    { key: "policy.hourly_max_minutes_per_request", value: 480, default: 480, labelFa: "سقف مدت هر درخواست مرخصی ساعتی (دقیقه)", group: "policy" },
    { key: "bradford.enabled", value: true, default: true, labelFa: "نمایش داشبورد Bradford (پس از تصویب سیاست سازمانی)", group: "bradford" },
    { key: "bradford.include_types", value: "sick", default: "sick", labelFa: "انواع مرخصی مشمول Bradford (کلیدها با ویرگول)", group: "bradford" },
    { key: "bradford.include_hourly", value: false, default: false, labelFa: "منظور کردن مرخصی ساعتی در Bradford", group: "bradford" },
    { key: "bradford.hourly_minutes_per_day", value: 480, default: 480, labelFa: "تبدیل دقیقه ساعتی به روز در Bradford", group: "bradford" },
    { key: "bradford.threshold_attention", value: 50, default: 50, labelFa: "آستانه نیازمند توجه (پیشنهادی)", group: "bradford" },
    { key: "bradford.threshold_warn", value: 200, default: 200, labelFa: "آستانه هشدار (پیشنهادی)", group: "bradford" },
    { key: "bradford.threshold_serious", value: 500, default: 500, labelFa: "آستانه هشدار جدی (پیشنهادی)", group: "bradford" },
    { key: "bradford.threshold_review", value: 1000, default: 1000, labelFa: "آستانه نیازمند بررسی HR (پیشنهادی)", group: "bradford" },
    { key: "holidays.source", value: "manual", default: "manual", labelFa: "منبع تعطیلات", group: "holidays" },
  ];
  return {
    users, sessionUserId: null, types, requests,
    comments: { 1: [{ id: 1, authorName: "سارا محمدی", body: "در صورت نیاز تماس بگیرید. (Demo)", createdAt: today }] },
    attachments: { 1: [{ id: 1, fileName: "note-demo.pdf", sizeLabel: "120 کیلوبایت", createdAt: today }] },
    entitlements, holidays,
    notifications: [
      { id: 1, title: "درخواست جدید", body: "درخواست مرخصی سارا محمدی در انتظار تأیید است. (Demo)", read: false, createdAt: today, requestId: 1, kind: "request_sent_manager", category: "request" },
      { id: 2, title: "تأیید مرخصی", body: "مرخصی شما تأیید شد. (Demo)", read: true, createdAt: addDaysIso(today, -18), requestId: 2, kind: "approved", category: "approval" },
      { id: 3, title: "مرخصی ساعتی", body: "درخواست مرخصی ساعتی سارا محمدی تأیید شد. (Demo)", read: false, createdAt: addDaysIso(today, -1), requestId: 6, kind: "hourly_created", category: "request" },
    ],
    employees, companies, departments,
    contracts: [{ id: 1, employeeId: 1, start: "2024-03-20", end: null, workDays: WORK, contractPercent: 100 }],
    settings,
    bradfordNotes: [],
    jobs: [
      { id: 1, job: "Reminder", started: addDaysIso(today, -1) + "T06:00:00Z", finished: addDaysIso(today, -1) + "T06:00:04Z", status: "skipped", error: "مهلت پیکربندی نشده (OQ-04)" },
      { id: 2, job: "YearRollover", started: addDaysIso(today, -40) + "T02:00:00Z", finished: addDaysIso(today, -40) + "T02:01:12Z", status: "ok", error: "" },
    ],
    audit: [
      { id: 1, actorName: "نرگس احمدی", action: "login", entity: "User", entityId: "3", reason: "", result: "ok", createdAt: today },
      { id: 2, actorName: "رضا کریمی", action: "approve", entity: "LeaveRequest", entityId: "2", reason: "", result: "ok", createdAt: addDaysIso(today, -18) },
    ],
    seq: 40,
  };
}

const EXTRA_SETTINGS: SettingItem[] = [
  { key: "workflow.auto_cancel_enabled", value: true, default: true, labelFa: "فعال بودن لغو خودکار درخواست‌های تأییدنشده", group: "workflow" },
  { key: "workflow.auto_cancel_pending_after_days", value: 3, default: 3, labelFa: "حداکثر مدت انتظار برای تأیید درخواست مرخصی (روز تقویمی)", group: "workflow" },
  { key: "policy.hourly_leave_enabled", value: true, default: true, labelFa: "فعال بودن مرخصی ساعتی", group: "policy" },
  { key: "policy.workday_start", value: "08:00", default: "08:00", labelFa: "شروع ساعات کاری", group: "policy" },
  { key: "policy.workday_end", value: "16:00", default: "16:00", labelFa: "پایان ساعات کاری", group: "policy" },
  { key: "policy.workday_minutes", value: 480, default: 480, labelFa: "دقایق روز کاری برای تبدیل مرخصی ساعتی", group: "policy" },
  { key: "policy.hourly_allow_outside_work_hours", value: false, default: false, labelFa: "اجازه مرخصی ساعتی خارج از ساعات کاری", group: "policy" },
  { key: "policy.hourly_allow_on_holiday", value: false, default: false, labelFa: "اجازه مرخصی ساعتی در تعطیل رسمی", group: "policy" },
  { key: "policy.hourly_max_minutes_per_request", value: 480, default: 480, labelFa: "سقف مدت هر درخواست مرخصی ساعتی (دقیقه)", group: "policy" },
  { key: "bradford.enabled", value: true, default: true, labelFa: "نمایش داشبورد Bradford (پس از تصویب سیاست سازمانی)", group: "bradford" },
  { key: "bradford.include_types", value: "sick", default: "sick", labelFa: "انواع مرخصی مشمول Bradford (کلیدها با ویرگول)", group: "bradford" },
  { key: "bradford.include_hourly", value: false, default: false, labelFa: "منظور کردن مرخصی ساعتی در Bradford", group: "bradford" },
  { key: "bradford.hourly_minutes_per_day", value: 480, default: 480, labelFa: "تبدیل دقیقه ساعتی به روز در Bradford", group: "bradford" },
  { key: "bradford.threshold_attention", value: 50, default: 50, labelFa: "آستانه نیازمند توجه (پیشنهادی)", group: "bradford" },
  { key: "bradford.threshold_warn", value: 200, default: 200, labelFa: "آستانه هشدار (پیشنهادی)", group: "bradford" },
  { key: "bradford.threshold_serious", value: 500, default: 500, labelFa: "آستانه هشدار جدی (پیشنهادی)", group: "bradford" },
  { key: "bradford.threshold_review", value: 1000, default: 1000, labelFa: "آستانه نیازمند بررسی HR (پیشنهادی)", group: "bradford" },
];

function emitNotify(): void {
  try { window.dispatchEvent(new Event("zsh-leave-notifications")); } catch { /* SSR */ }
}

function policy(): ReturnType<typeof clientPolicyFromSettings> {
  return clientPolicyFromSettings(db.settings);
}

function hydrate(store: Store): Store {
  if (!store.bradfordNotes) store.bradfordNotes = [];
  for (const s of EXTRA_SETTINGS) {
    if (!store.settings.some((x) => x.key === s.key)) store.settings.push({ ...s });
  }
  return store;
}

function load(): Store {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (raw) return hydrate(JSON.parse(raw) as Store);
  } catch { /* ignore */ }
  return seed();
}

let db = load();

function save(): void {
  sessionStorage.setItem(KEY, JSON.stringify(db));
}

function nextId(): number {
  db.seq += 1;
  return db.seq;
}

function current(): User {
  const u = db.users.find((x) => x.id === db.sessionUserId);
  if (!u) fail(401, "نشست معتبر نیست", "unauthenticated");
  return u;
}

function has(u: User, ...roles: Role[]): boolean {
  return roles.some((r) => u.roles.includes(r));
}

function redact(u: User, r: LeaveRequest): LeaveRequest | null {
  if (has(u, "admin", "exec")) return null;
  const own = u.employeeId === r.employeeId;
  const mgr = has(u, "manager") && !own;
  const hr = has(u, "hr");
  if (!own && !mgr && !hr) return null;
  if (r.confidential && !hr) {
    return { ...r, type: undefined, typeLabel: "غایب", reason: "", decisionComment: "", replacementName: null, replacementId: null, canDecide: false };
  }
  return { ...r, canDecide: (hr || (mgr && r.status === "PENDING" && !r.confidential)) };
}

function typeByKey(key: string): LeaveType {
  const t = db.types.find((x) => x.key === key);
  if (!t) fail(422, "نوع مرخصی نامعتبر است", "validation");
  return t;
}

function holidayIsos(): string[] {
  return db.holidays.map((h) => {
    const y = h.jalaliYear ?? jalaliNow().jy;
    try {
      return gToIso(toGregorian(y, h.jalaliMonth, h.jalaliDay));
    } catch {
      return "";
    }
  }).filter(Boolean);
}

function computeBalance(employeeId: number, typeKey: string, year: number): Balance {
  const t = typeByKey(typeKey);
  const ent = db.entitlements.find((e) => e.employeeId === employeeId && e.leaveTypeKey === typeKey && e.year === year);
  const base = Number(ent?.base ?? 0);
  const carry = Number(ent?.carryOver ?? 0);
  const adj = Number(ent?.adjustment ?? 0);
  const total = base + carry + adj;
  const used = db.requests
    .filter((r) => r.employeeId === employeeId && r.type === typeKey && (r.status === "APPROVED" || r.status === "WITHDRAWAL_PENDING") && t.countsAgainstBalance)
    .reduce((s, r) => s + Number(r.workingDays), 0);
  const pending = db.requests
    .filter((r) => r.employeeId === employeeId && r.type === typeKey && (r.status === "PENDING" || r.status === "ESCALATED") && t.countsAgainstBalance)
    .reduce((s, r) => s + Number(r.workingDays), 0);
  return {
    leaveTypeKey: typeKey, leaveTypeLabel: t.labelFa, year,
    total: total.toFixed(1), used: used.toFixed(1), pending: pending.toFixed(1),
    remaining: (total - used).toFixed(1), usable: (total - used - pending).toFixed(1),
    base: base.toFixed(1), carryOver: carry.toFixed(1), adjustment: adj.toFixed(1),
  };
}

function validateDraft(d: LeaveDraft, t: LeaveType, actor: User): void {
  if (d.start > d.end) fail(422, "تاریخ شروع نباید بعد از پایان باشد", "validation");
  if (Number(d.workingDays) <= 0) fail(422, "روز کاری باید مثبت باشد", "validation");
  if (t.requiresNote && !d.reason.trim()) fail(422, "توضیح برای این نوع الزامی است", "validation");
  if (t.requiresReplacement && !d.replacementId) fail(422, "انتخاب جانشین الزامی است", "validation");
  if (t.confidential && !has(actor, "hr")) fail(403, "نوع محرمانه فقط توسط HR ثبت می‌شود", "forbidden");
  if (!t.enabled) fail(422, "این نوع مرخصی غیرفعال است", "validation");
}

function notify(title: string, body: string, requestId?: number, kind?: NotifKind): void {
  const k = inferKind(title, body, kind);
  db.notifications.unshift({
    id: nextId(), title, body, read: false, createdAt: todayIso(), requestId, kind: k, category: KIND_CATEGORY[k],
  });
  emitNotify();
}

/** شبیه‌سازی Job لغو خودکار فقط وقتی جلسه Demo باز است؛ ادعای اجرا در مرورگر بسته نیست. */
function applyAutoCancelSimulation(): void {
  const now = todayIso();
  for (const r of db.requests) {
    const decision = decideAutoCancel(r, now);
    if (decision.action === "cancel" && (r.status === "PENDING" || r.status === "ESCALATED")) {
      r.status = AUTO_CANCELLED;
      r.canDecide = false;
      r.autoCancelledAt = now;
      r.autoCancelReason = decision.reason;
      r.expiresAt = decision.expiresAt;
      r.updatedAt = now;
      notify("لغو خودکار درخواست", `درخواست ${r.id} به‌خاطر پایان مهلت تأیید لغو شد. (Demo)`, r.id, "auto_cancelled");
      audit("auto_cancel", "LeaveRequest", String(r.id), decision.reason);
    } else if (decision.action === "wait") {
      r.expiresAt = decision.expiresAt;
      if (decision.remainingDays <= 1 && !r.expiryReminded) {
        r.expiryReminded = true;
        notify("نزدیک شدن مهلت تأیید", `مهلت تأیید درخواست ${r.id} رو به پایان است. (Demo)`, r.id, "expiry_soon");
      }
    }
  }
}

function audit(action: string, entity: string, entityId: string, reason = ""): void {
  const u = db.users.find((x) => x.id === db.sessionUserId);
  db.audit.unshift({ id: nextId(), actorName: u?.displayName ?? null, action, entity, entityId, reason, result: "ok", createdAt: todayIso() });
}

export const demoApi: Api = {
  async login(username) {
    const u = db.users.find((x) => x.username === username.trim());
    if (!u) fail(400, "نام کاربری یا رمز عبور نادرست است", "invalid_credentials");
    db.sessionUserId = u.id;
    save();
    audit("login", "User", String(u.id));
    return u;
  },
  async logout() {
    db.sessionUserId = null;
    save();
  },
  async me() { return current(); },
  async changePassword(currentPw, next) {
    current();
    if (!currentPw || next.length < 10) fail(422, "رمز جدید باید حداقل ۱۰ نویسه باشد", "validation");
    audit("password_change", "User", String(db.sessionUserId));
    save();
  },
  async listTypes() {
    const u = current();
    if (has(u, "admin")) return db.types;
    if (has(u, "hr")) return db.types.filter((t) => t.enabled);
    if (has(u, "employee", "manager")) return db.types.filter((t) => t.enabled && t.employeeRequestable);
    fail(403, "مشاهده انواع مرخصی مجاز نیست", "permission_denied");
  },
  async saveType(t) {
    const u = current();
    if (!has(u, "admin")) fail(403, "مدیریت انواع مرخصی مجاز نیست", "permission_denied");
    const reason = t.reason.trim();
    const reasonLength = Array.from(reason).length;
    if (!reasonLength || reasonLength > 500) fail(400, "دلیل تغییر باید بین ۱ تا ۵۰۰ نویسه باشد", "invalid_leave_type");
    if (t.confidential && t.employeeRequestable) fail(422, "نوع محرمانه نمی‌تواند توسط کارمند درخواست شود", "validation");
    const existing = db.types.find((x) => x.id === t.id || x.key === t.key);
    const { reason: _reason, ...change } = t;
    if (existing) Object.assign(existing, change);
    else db.types.push({
      id: nextId(), key: t.key, labelFa: t.labelFa, color: t.color ?? "#0f4c5c",
      countsAgainstBalance: !!t.countsAgainstBalance, requiresApproval: t.requiresApproval !== false,
      requiresNote: !!t.requiresNote, requiresReplacement: !!t.requiresReplacement,
      employeeRequestable: t.employeeRequestable !== false, confidential: !!t.confidential,
      enabled: t.enabled !== false, sortOrder: t.sortOrder ?? 10,
    });
    audit("leave_type_save", "LeaveType", t.key, reason);
    save();
    return db.types.find((x) => x.key === t.key)!;
  },
  async listRequests(f: RequestFilters) {
    const u = current();
    applyAutoCancelSimulation();
    save();
    let rows = db.requests.map((r) => redact(u, r)).filter((x): x is LeaveRequest => !!x);
    if (f.mine) rows = rows.filter((r) => r.employeeId === u.employeeId);
    if (f.status) rows = rows.filter((r) => r.status === f.status);
    if (f.q) {
      const q = f.q.trim();
      rows = rows.filter((r) => r.employeeName.includes(q) || (r.typeLabel ?? "").includes(q) || String(r.id) === q);
    }
    return { results: rows, count: rows.length, page: f.page ?? 1, pageSize: 50 };
  },
  async getRequest(id) {
    const u = current();
    applyAutoCancelSimulation();
    save();
    const r = db.requests.find((x) => x.id === id);
    const out = r ? redact(u, r) : null;
    if (!out) fail(404, "یافت نشد", "not_found");
    return out;
  },
  async createRequest(d) {
    const u = current();
    if (has(u, "admin", "exec") || !u.isEmployee) fail(404, "یافت نشد", "not_found");
    const t = typeByKey(d.leaveTypeKey);
    if (!t.employeeRequestable) fail(422, "این نوع برای درخواست کارمند فعال نیست", "validation");
    const pol = policy();
    const hourly = d.durationKind === "hourly";
    if (hourly && !pol.hourlyLeaveEnabled) fail(422, "مرخصی ساعتی در تنظیمات غیرفعال است", "validation");
    if (hourly) {
      const dur = minutesBetween(d.startTime ?? "", d.endTime ?? "");
      if (dur == null || dur <= 0) fail(422, "ساعت پایان باید بعد از ساعت شروع باشد", "validation");
      const herr = validateHourlyDraft(
        { date: d.start, startTime: d.startTime ?? "", endTime: d.endTime ?? "" },
        {
          workdayStart: pol.workdayStart, workdayEnd: pol.workdayEnd,
          allowOutsideWorkHours: pol.hourlyAllowOutsideWorkHours, allowOnHoliday: pol.hourlyAllowOnHoliday,
          maxMinutesPerRequest: pol.hourlyMaxMinutesPerRequest, workWeekdays: WORK,
        },
        holidayIsos(),
      );
      const first = Object.values(herr)[0];
      if (first) fail(422, first, "validation");
      const clash = findHourlyConflict(u.employeeId!, { date: d.start, startTime: d.startTime ?? "", endTime: d.endTime ?? "" }, db.requests);
      if (clash) fail(409, "این بازه ساعتی با درخواست دیگری تداخل دارد", "conflict");
      d.end = d.start;
      d.durationMinutes = dur;
      d.workingDays = workingDaysFromMinutes(dur, pol.workdayMinutes);
    } else {
      const clash = findDailyConflict(u.employeeId!, d.start, d.end, db.requests);
      if (clash) fail(409, "این بازه با درخواست دیگری تداخل دارد", "conflict");
    }
    validateDraft(d, t, u);
    const emp = db.employees.find((e) => e.id === u.employeeId)!;
    const status = !t.requiresApproval ? "APPROVED" : !emp.managerName ? "ESCALATED" : "PENDING";
    const nSnap = snapshotAutoCancelDays(pol.autoCancelEnabled, pol.autoCancelDays ?? 0);
    const created = todayIso();
    const row: LeaveRequest = {
      id: nextId(), employeeId: emp.id, employeeName: emp.displayName, departmentName: emp.departmentName,
      status, start: d.start, end: d.end, workingDays: Number(d.workingDays).toFixed(hourly ? 2 : 1),
      type: t.key, typeLabel: t.labelFa, reason: d.reason, canDecide: status === "PENDING",
      replacementId: d.replacementId ?? null,
      replacementName: db.employees.find((e) => e.id === d.replacementId)?.displayName ?? null,
      createdAt: created, updatedAt: created, confidential: t.confidential,
      durationKind: hourly ? "hourly" : "daily",
      startTime: hourly ? d.startTime : undefined,
      endTime: hourly ? d.endTime : undefined,
      durationMinutes: hourly ? d.durationMinutes : undefined,
      autoCancelPolicyDays: nSnap,
      expiresAt: nSnap ? addDaysIso(created, nSnap) : undefined,
    };
    db.requests.unshift(row);
    notify(
      hourly ? "درخواست مرخصی ساعتی" : "درخواست مرخصی",
      `درخواست ${emp.displayName} ثبت شد. (Demo)`,
      row.id,
      hourly ? "hourly_created" : "request_created",
    );
    if (status === "PENDING" || status === "ESCALATED") {
      notify("ارسال برای بررسی", `درخواست ${row.id} برای مدیر/HR ارسال شد. (Demo)`, row.id, "request_sent_manager");
    }
    audit("create_request", "LeaveRequest", String(row.id));
    save();
    return row;
  },
  async updateRequest(id, d) {
    const u = current();
    const r = db.requests.find((x) => x.id === id);
    if (!r || r.employeeId !== u.employeeId) fail(404, "یافت نشد", "not_found");
    if (r.status !== "PENDING" && r.status !== "ESCALATED") fail(409, "فقط درخواست در انتظار قابل ویرایش است", "conflict");
    if (d.durationKind === "hourly" || r.durationKind === "hourly") {
      const start = d.start ?? r.start;
      const startTime = d.startTime ?? r.startTime ?? "";
      const endTime = d.endTime ?? r.endTime ?? "";
      const clash = findHourlyConflict(r.employeeId, { date: start, startTime, endTime }, db.requests, r.id);
      if (clash) fail(409, "این بازه ساعتی با درخواست دیگری تداخل دارد", "conflict");
    }
    Object.assign(r, d, { updatedAt: todayIso() });
    save();
    return r;
  },
  async actRequest(id, action, reason = "") {
    const u = current();
    const r = db.requests.find((x) => x.id === id);
    const view = r ? redact(u, r) : null;
    if (!r || !view) fail(404, "یافت نشد", "not_found");
    const own = u.employeeId === r.employeeId;
    const hr = has(u, "hr");
    const next = { ...r };
    if (action === "approve") {
      if (!(view.canDecide) || (r.status !== "PENDING" && r.status !== "ESCALATED")) fail(409, "انتقال نامعتبر است", "invalid_transition");
      if (r.status === "ESCALATED" && !hr) fail(404, "یافت نشد", "not_found");
      next.status = "APPROVED";
    } else if (action === "reject") {
      if (!reason.trim()) fail(422, "درج دلیل رد الزامی است", "validation");
      if (!view.canDecide) fail(404, "یافت نشد", "not_found");
      next.status = "REJECTED";
      next.decisionComment = reason;
    } else if (action === "cancel") {
      if (!own && !hr) fail(404, "یافت نشد", "not_found");
      if (r.status !== "PENDING" && r.status !== "ESCALATED") fail(409, "درخواست تأییدشده با درخواست لغو لغو می‌شود", "invalid_transition");
      next.status = "CANCELLED";
    } else if (action === "withdraw") {
      if (!own && !hr) fail(404, "یافت نشد", "not_found");
      if (r.status !== "APPROVED") fail(409, "لغو فقط برای مرخصی تأییدشده", "invalid_transition");
      next.status = "WITHDRAWAL_PENDING";
    } else if (action === "amend") {
      if (r.status !== "APPROVED") fail(409, "فقط مرخصی تأییدشده قابل اصلاح است", "invalid_transition");
      if (db.requests.some((x) => x.supersedesId === r.id && x.status !== "REJECTED" && x.status !== "CANCELLED")) {
        fail(409, "یک اصلاح فعال برای این درخواست وجود دارد", "conflict");
      }
      const copy: LeaveRequest = { ...r, id: nextId(), status: "PENDING", supersedesId: r.id, createdAt: todayIso(), updatedAt: todayIso(), reason: (r.reason ?? "") + " — درخواست اصلاح (Demo)" };
      db.requests.unshift(copy);
      audit("amend", "LeaveRequest", String(copy.id));
      save();
      return copy;
    } else if (action === "escalate") {
      if (!hr) fail(404, "یافت نشد", "not_found");
      if (r.status !== "PENDING") fail(409, "فقط PENDING ارجاع می‌شود", "invalid_transition");
      next.status = "ESCALATED";
    } else {
      fail(422, "رویداد ناشناخته", "validation");
    }
    Object.assign(r, next, { updatedAt: todayIso(), decisionComment: next.decisionComment ?? r.decisionComment });
    const kindMap: Record<string, NotifKind> = {
      APPROVED: "approved", REJECTED: "rejected", CANCELLED: "cancelled",
      ESCALATED: "pending", WITHDRAWAL_PENDING: "pending", AUTO_CANCELLED: "auto_cancelled",
    };
    notify("به‌روزرسانی درخواست", `وضعیت درخواست ${r.id} به ${r.status} تغییر کرد. (Demo)`, r.id, kindMap[r.status] ?? "pending");
    audit(action, "LeaveRequest", String(r.id), reason);
    save();
    return redact(u, r)!;
  },
  async listComments(id) {
    await this.getRequest(id);
    return db.comments[id] ?? [];
  },
  async addComment(id, body) {
    await this.getRequest(id);
    if (!body.trim()) fail(422, "متن نظر خالی است", "validation");
    const c: Comment = { id: nextId(), authorName: current().displayName, body: body.trim(), createdAt: todayIso() };
    db.comments[id] = [...(db.comments[id] ?? []), c];
    save();
    return c;
  },
  async listAttachments(id) {
    await this.getRequest(id);
    return db.attachments[id] ?? [];
  },
  async addAttachment(id, file) {
    await this.getRequest(id);
    const a: Attachment = { id: nextId(), fileName: file.name, sizeLabel: "نمایشی", createdAt: todayIso() };
    db.attachments[id] = [...(db.attachments[id] ?? []), a];
    save();
    return a;
  },
  async balances(year, employeeId) {
    const u = current();
    if (has(u, "admin")) fail(404, "یافت نشد", "not_found");
    const id = employeeId ?? u.employeeId;
    if (!id) fail(404, "یافت نشد", "not_found");
    if (!has(u, "hr", "manager", "exec") && id !== u.employeeId) fail(404, "یافت نشد", "not_found");
    const types = db.types.filter((t) => t.countsAgainstBalance && (has(u, "hr") || !t.confidential));
    return types.map((t) => computeBalance(id, t.key, year));
  },
  async entitlements(year) {
    const u = current();
    if (!has(u, "hr")) fail(404, "یافت نشد", "not_found");
    return db.entitlements.filter((e) => e.year === year);
  },
  async adjustEntitlement(id, days, reason) {
    const u = current();
    if (!has(u, "hr")) fail(404, "یافت نشد", "not_found");
    if (!reason.trim()) fail(422, "دلیل اصلاح الزامی است", "validation");
    const e = db.entitlements.find((x) => x.id === id);
    if (!e) fail(404, "یافت نشد", "not_found");
    e.adjustment = (Number(e.adjustment) + Number(days)).toFixed(1);
    audit("adjust", "LeaveEntitlement", String(id), reason);
    save();
    return e;
  },
  async importEntitlements(csv, dryRun) {
    current();
    if (!has(current(), "hr")) fail(404, "یافت نشد", "not_found");
    const lines = csv.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    const rows: ImportPreview["rows"] = [];
    for (let i = 0; i < lines.length; i++) {
      if (i === 0 && /personnel|کد/i.test(lines[i])) continue;
      const [code, type, year, days] = lines[i].split(",").map((x) => x.trim());
      const emp = db.employees.find((e) => e.personnelCode === code);
      const ok = !!emp && !!type && !!Number(year) && !!Number(days);
      rows.push({
        line: i + 1, employee: emp?.displayName ?? code, type, year: Number(year) || 0, days: days ?? "",
        ok, error: ok ? undefined : "ردیف نامعتبر یا کارمند ناشناخته",
      });
    }
    const rejected = rows.filter((r) => !r.ok).length;
    if (!dryRun && rejected === 0) {
      rows.forEach((r) => {
        const emp = db.employees.find((e) => e.displayName === r.employee);
        if (!emp) return;
        const ent = db.entitlements.find((e) => e.employeeId === emp.id && e.leaveTypeKey === r.type && e.year === r.year);
        if (ent) ent.base = Number(r.days).toFixed(1);
      });
      audit("import", "ImportBatch", String(nextId()));
    }
    save();
    return { dryRun, rows, accepted: rows.filter((r) => r.ok).length, rejected };
  },
  async recordAbsence(d) {
    const u = current();
    if (!has(u, "hr")) fail(404, "یافت نشد", "not_found");
    const t = typeByKey(d.leaveTypeKey);
    const emp = db.employees.find((e) => e.id === d.employeeId);
    if (!emp) fail(422, "کارمند انتخاب نشده", "validation");
    const row: LeaveRequest = {
      id: nextId(), employeeId: emp.id, employeeName: emp.displayName, departmentName: emp.departmentName,
      status: "APPROVED", start: d.start, end: d.end, workingDays: Number(d.workingDays).toFixed(1),
      type: t.key, typeLabel: t.confidential ? t.labelFa : t.labelFa, reason: d.reason, confidential: t.confidential,
      createdAt: todayIso(), updatedAt: todayIso(), canDecide: false,
    };
    db.requests.unshift(row);
    audit("hr_absence", "LeaveRequest", String(row.id), d.reason);
    save();
    return row;
  },
  async calendar(from, to, scope) {
    const u = current();
    const peersSeeType = db.settings.find((s) => s.key === "privacy.peers_see_leave_type")?.value === true;
    let rows = db.requests.filter((r) => r.status !== "REJECTED" && r.status !== "CANCELLED" && r.status !== AUTO_CANCELLED && r.start <= to && r.end >= from);
    if (scope === "me") rows = rows.filter((r) => r.employeeId === u.employeeId);
    if (has(u, "admin")) rows = [];
    return rows.map((r) => {
      const own = r.employeeId === u.employeeId;
      const hr = has(u, "hr");
      const hideType = r.confidential ? !hr : (!own && !hr && !peersSeeType);
      return {
        id: r.id, employeeId: r.employeeId, employeeName: r.employeeName, start: r.start, end: r.end,
        status: r.status, label: hideType ? "غایب" : (r.durationKind === "hourly" ? `${r.typeLabel ?? "غایب"} ساعتی` : (r.typeLabel ?? "غایب")),
        color: hideType ? "#6b7280" : (db.types.find((t) => t.key === r.type)?.color ?? "#0f4c5c"),
        scope, durationKind: r.durationKind, startTime: r.startTime, endTime: r.endTime,
      } satisfies CalendarEvent;
    });
  },
  async calendarHolidays(from, to) {
    current();
    const result: CalendarHolidayEvent[] = [];
    for (let iso = from; iso <= to; iso = addDaysIso(iso, 1)) {
      const { jy, jm, jd } = fromGregorian(isoToG(iso));
      for (const holiday of db.holidays) {
        if (holiday.jalaliMonth === jm && holiday.jalaliDay === jd &&
            (holiday.jalaliYear == null || holiday.jalaliYear === jy)) {
          result.push({
            id: `legacy-${holiday.id}-${iso}`,
            title: holiday.label,
            start: iso,
            end: iso,
            scope: holiday.scope,
            kind: "official",
          });
        }
      }
    }
    return result;
  },
  async holidays() { current(); return db.holidays; },
  async saveHoliday(h) {
    const u = current();
    if (!has(u, "hr", "admin")) fail(404, "یافت نشد", "not_found");
    if (h.id) {
      const row = db.holidays.find((x) => x.id === h.id);
      if (!row) fail(404, "یافت نشد", "not_found");
      Object.assign(row, h);
      save();
      return row;
    }
    const row: Holiday = { ...h, id: nextId() };
    db.holidays.push(row);
    audit("holiday_save", "PublicHoliday", String(row.id));
    save();
    return row;
  },
  async deleteHoliday(id, reason) {
    const u = current();
    if (!has(u, "hr", "admin")) fail(404, "یافت نشد", "not_found");
    db.holidays = db.holidays.filter((h) => h.id !== id);
    audit("holiday_deactivate", "PublicHoliday", String(id), reason);
    save();
  },
  async notifications() {
    const u = current();
    // شبیه‌سازی Job لغو خودکار: فقط وقتی جلسه Demo باز است و اعلان‌ها نظرسنجی می‌شوند
    applyAutoCancelSimulation();
    save();
    return db.notifications.filter((n) => {
      if (has(u, "manager", "hr") ) return true;
      return n.requestId ? db.requests.some((r) => r.id === n.requestId && r.employeeId === u.employeeId) : true;
    });
  },
  async readNotification(id) {
    const n = db.notifications.find((x) => x.id === id);
    if (n) n.read = true;
    save();
  },
  async employees(q) {
    const u = current();
    if (has(u, "admin")) return db.employees;
    if (!has(u, "hr", "manager")) {
      return db.employees.filter((e) => e.isActive && e.id !== u.employeeId);
    }
    const qq = (q ?? "").trim();
    return db.employees.filter((e) => !qq || e.displayName.includes(qq) || e.personnelCode.includes(qq));
  },
  async saveEmployee(e) {
    current();
    void e;
    fail(501, "دعوت و فعال‌سازی واقعی در حالت Demo انجام نمی‌شود", "demo_onboarding_unavailable");
  },
  async inviteEmployee(id, email) {
    current();
    void id;
    void email;
    fail(501, "ارسال دعوت واقعی در حالت Demo انجام نمی‌شود", "demo_onboarding_unavailable");
  },
  async revokeEmployeeInvitation(id, reason) {
    current();
    void id;
    void reason;
    fail(501, "مدیریت دعوت واقعی در حالت Demo انجام نمی‌شود", "demo_onboarding_unavailable");
  },
  async suspendEmployee(id, reason) {
    current();
    void id;
    void reason;
    fail(501, "تعلیق حساب واقعی در حالت Demo انجام نمی‌شود", "demo_onboarding_unavailable");
  },
  async activateAccount(token, email, password) {
    void token;
    void email;
    void password;
    fail(501, "فعال‌سازی حساب واقعی در حالت Demo انجام نمی‌شود", "demo_onboarding_unavailable");
  },
  async companies() { current(); return db.companies; },
  async departments() { current(); return db.departments; },
  async sites() { current(); return []; },
  async saveCompany(c) {
    if (!has(current(), "admin")) fail(404, "یافت نشد", "not_found");
    if (c.id) {
      const row = db.companies.find((x) => x.id === c.id)!;
      Object.assign(row, c);
      save();
      return row;
    }
    const row: Company = { id: nextId(), code: c.code, name: c.name, isActive: c.isActive !== false };
    db.companies.push(row);
    save();
    return row;
  },
  async saveDepartment(d) {
    if (!has(current(), "admin")) fail(404, "یافت نشد", "not_found");
    const row: Department = d.id
      ? Object.assign(db.departments.find((x) => x.id === d.id)!, d)
      : { id: nextId(), companyId: d.companyId, code: d.code, name: d.name, parentId: d.parentId ?? null };
    if (!d.id) db.departments.push(row);
    save();
    return row;
  },
  async contracts(employeeId) {
    if (!has(current(), "hr")) fail(404, "یافت نشد", "not_found");
    return db.contracts.filter((c) => c.employeeId === employeeId);
  },
  async saveContract(p) {
    if (!has(current(), "hr")) fail(404, "یافت نشد", "not_found");
    const overlap = db.contracts.some((c) => c.employeeId === p.employeeId && c.id !== p.id && c.start <= (p.end ?? "9999-12-31") && (c.end ?? "9999-12-31") >= p.start);
    if (overlap) fail(409, "بازه قرارداد هم‌پوشانی دارد", "conflict");
    if (p.id) {
      const row = db.contracts.find((x) => x.id === p.id)!;
      Object.assign(row, p);
      save();
      return row;
    }
    const row: ContractPeriod = { ...p, id: nextId() };
    db.contracts.push(row);
    save();
    return row;
  },
  async coverage(from, to) {
    const u = current();
    if (!has(u, "manager", "hr")) fail(404, "یافت نشد", "not_found");
    const days: CoverageRow[] = [];
    let d = from;
    while (d <= to) {
      const away = db.requests.filter((r) => ["APPROVED", "PENDING", "ESCALATED", "WITHDRAWAL_PENDING"].includes(r.status) && r.start <= d && r.end >= d).length;
      days.push({ date: d, departmentName: "همه واحدها (Demo)", awayCount: away, headcount: db.employees.length });
      d = addDaysIso(d, 1);
      if (days.length > 21) break;
    }
    return days;
  },
  async reports(kind) {
    const u = current();
    if (!has(u, "hr", "exec")) fail(404, "یافت نشد", "not_found");
    const minG = Number(db.settings.find((s) => s.key === "privacy.min_group_size")?.value ?? 5);
    if (kind === "bradford") {
      // محاسبه از داده‌ی واقعیِ همین Store‌ی Demo (نه اعداد ثابت)؛ همان تحلیل داشبورد HR
      const cfg = bradfordConfigFromSettings(db.settings);
      const active = db.employees.filter((e) => e.isActive);
      if (has(u, "exec") && active.length < minG) {
        return { title: "Bradford Factor (تجمیعی)", note: `گروه کوچک‌تر از ${minG} نفر سرکوب شد.`, series: [] };
      }
      const hol = new Set(holidayIsos());
      const win = { from: addDaysIso(today, -364), to: today };
      const results = active.map((e) => analyzeEmployee(
        db.requests.filter((r) => r.employeeId === e.id),
        win, { workWeekdays: WORK, holidays: hol }, cfg, today,
      ));
      const counts = levelCounts(results);
      return {
        title: "Bradford Factor (تجمیعی، ۳۶۵ روز)",
        note: `ابزار تحلیلی است، نه مبنای تصمیم. جزئیات فردی فقط در داشبورد HR. محاسبه از داده Demo؛ انواع مشمول: ${cfg.includeTypes.join("، ") || "—"}.`,
        series: (Object.keys(counts) as (keyof typeof counts)[]).map((k) => ({ label: BRADFORD_LEVEL_LABEL[k], value: counts[k] })),
      };
    }
    if (kind === "process-health") {
      return { title: "سلامت فرآیند", series: [
        { label: "میانگین روز تصمیم (تخمین Demo)", value: 2.4 },
        { label: "ارجاع‌شده", value: db.requests.filter((r) => r.status === "ESCALATED").length },
      ] };
    }
    const approved = db.requests.filter((r) => r.status === "APPROVED").length;
    if (has(u, "exec") && db.employees.length < minG) {
      return { title: "گزارش تجمیعی", note: `گروه کوچک‌تر از ${minG} نفر سرکوب شد.`, series: [] };
    }
    const hourly = db.requests.filter((r) => r.durationKind === "hourly").length;
    const daily = db.requests.length - hourly;
    return { title: kind === "balances" ? "مانده‌ها" : kind === "trends" ? "روند و ترکیب" : "غیبت‌ها", series: [
      { label: "تأییدشده", value: approved },
      { label: "در انتظار", value: db.requests.filter((r) => r.status === "PENDING").length },
      { label: "ارجاع‌شده", value: db.requests.filter((r) => r.status === "ESCALATED").length },
      { label: "ردشده", value: db.requests.filter((r) => r.status === "REJECTED").length },
      { label: "لغوشده", value: db.requests.filter((r) => r.status === "CANCELLED").length },
      { label: "لغوشده خودکار", value: db.requests.filter((r) => r.status === AUTO_CANCELLED).length },
      { label: "روزانه", value: daily },
      { label: "ساعتی", value: hourly },
    ] };
  },
  async settings() {
    const u = current();
    if (!has(u, "admin", "hr")) fail(404, "یافت نشد", "not_found");
    if (has(u, "admin")) return db.settings;
    return db.settings.filter((s) => s.group === "workflow" || s.group === "bradford" || s.key.startsWith("policy.hourly") || s.key.startsWith("policy.workday"));
  },
  async saveSetting(key, value, reason) {
    const u = current();
    if (!has(u, "admin", "hr")) fail(404, "یافت نشد", "not_found");
    if (!reason.trim()) fail(422, "دلیل تغییر الزامی است", "validation");
    const row = db.settings.find((s) => s.key === key);
    if (!row) fail(422, "کلید ناشناخته", "validation");
    if (!has(u, "admin") && !(row.group === "workflow" || row.group === "bradford" || row.key.startsWith("policy.hourly") || row.key.startsWith("policy.workday"))) {
      fail(404, "یافت نشد", "not_found");
    }
    if (key === "workflow.auto_cancel_pending_after_days") {
      const n = Number(value);
      if (!Number.isInteger(n) || n < 1) fail(422, "تعداد روز باید عدد صحیح بزرگ‌تر از صفر باشد", "validation");
    }
    if (key === "policy.hourly_max_minutes_per_request" || key === "policy.workday_minutes" || key.startsWith("bradford.threshold") || key === "bradford.hourly_minutes_per_day") {
      const n = Number(value);
      if (!Number.isFinite(n) || n <= 0) fail(422, "مقدار باید عدد مثبت باشد", "validation");
    }
    row.value = value;
    audit("setting_change", "SystemSetting", key, reason);
    save();
    return row;
  },
  async clientPolicy() {
    current();
    return clientPolicyFromSettings(db.settings);
  },
  async bradfordNotes() {
    if (!has(current(), "hr")) fail(404, "یافت نشد", "not_found");
    return db.bradfordNotes;
  },
  async saveBradfordNote(employeeId, body) {
    const u = current();
    if (!has(u, "hr")) fail(404, "یافت نشد", "not_found");
    const now = todayIso();
    const existing = db.bradfordNotes.find((n) => n.employeeId === employeeId);
    if (existing) {
      existing.body = body;
      existing.updatedAt = now;
      existing.authorName = u.displayName;
      save();
      return existing;
    }
    const row: BradfordNote = { employeeId, body, updatedAt: now, authorName: u.displayName };
    db.bradfordNotes.push(row);
    save();
    return row;
  },
  async jobs() {
    if (!has(current(), "admin")) fail(404, "یافت نشد", "not_found");
    return db.jobs;
  },
  async audit() {
    const u = current();
    if (!has(u, "admin", "hr")) fail(404, "یافت نشد", "not_found");
    return db.audit;
  },
  async health() {
    return { live: true, ready: true, db: "demo", lastJob: db.jobs[0]?.job ?? "—" };
  },
  async estimate(employeeId, start, end) {
    current();
    void employeeId;
    return estimateWorkingDays(start, end, WORK, holidayIsos());
  },
};

export function resetDemo(): void {
  db = seed();
  save();
}

export const DEMO_ACCOUNTS = [
  { username: "sara", role: "کارمند" },
  { username: "reza", role: "مدیر مستقیم" },
  { username: "narges", role: "منابع انسانی" },
  { username: "admin", role: "مدیر سامانه" },
  { username: "exec", role: "گزارش‌خوان ارشد" },
];
