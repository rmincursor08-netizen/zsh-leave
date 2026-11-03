import type { Role } from "../lib/roles";

export type ApiErrorBody = {
  code: string;
  message_fa: string;
  fields?: Record<string, string>;
};

export type User = {
  id: number;
  username: string;
  displayName: string;
  email: string;
  roles: Role[];
  employeeId: number | null;
  companyId: number | null;
  companyName: string;
  departmentName: string;
  personnelCode: string;
  managerName: string | null;
  isEmployee: boolean;
};

export type LeaveType = {
  id: number;
  key: string;
  labelFa: string;
  color: string;
  countsAgainstBalance: boolean;
  requiresApproval: boolean;
  requiresNote: boolean;
  requiresReplacement: boolean;
  employeeRequestable: boolean;
  confidential: boolean;
  enabled: boolean;
  sortOrder: number;
};

export type LeaveRequest = {
  id: number;
  employeeId: number;
  employeeName: string;
  departmentName: string;
  status: string;
  start: string;
  end: string;
  workingDays: string;
  type?: string;
  typeLabel?: string;
  reason?: string;
  decisionComment?: string;
  replacementId?: number | null;
  replacementName?: string | null;
  canDecide?: boolean;
  confidential?: boolean;
  supersedesId?: number | null;
  createdAt: string;
  updatedAt: string;
  durationKind?: "daily" | "hourly";
  startTime?: string;
  endTime?: string;
  durationMinutes?: number;
  /** N ذخیره‌شده هنگام ثبت؛ null یعنی این درخواست مشمول سیاست لغو خودکار نبوده است. */
  autoCancelPolicyDays?: number | null;
  expiresAt?: string;
  autoCancelledAt?: string;
  autoCancelReason?: string;
  expiryReminded?: boolean;
};

export type Comment = {
  id: number;
  authorName: string;
  body: string;
  createdAt: string;
};

export type Attachment = {
  id: number;
  fileName: string;
  sizeLabel: string;
  createdAt: string;
  scanStatus?: "pending_scan" | "scanning" | "clean" | "rejected" | "scanner_unavailable" | "scan_failed" | "deleted";
  downloadUrl?: string;
};

export type Balance = {
  leaveTypeKey: string;
  leaveTypeLabel: string;
  year: number;
  total: string;
  used: string;
  pending: string;
  remaining: string;
  usable: string;
  base: string;
  carryOver: string;
  adjustment: string;
};

export type Entitlement = {
  id: number;
  employeeId: number;
  employeeName: string;
  leaveTypeKey: string;
  leaveTypeLabel: string;
  year: number;
  base: string;
  carryOver: string;
  adjustment: string;
};

export type CalendarEvent = {
  id: number;
  employeeId: number;
  employeeName: string;
  start: string;
  end: string;
  status: string;
  label: string;
  color: string;
  scope: "me" | "team" | "org";
  durationKind?: "daily" | "hourly";
  startTime?: string;
  endTime?: string;
};

export type CalendarHolidayEvent = {
  id: number | string;
  title: string;
  start: string;
  end: string;
  scope: string;
  kind: string;
};

export type Holiday = {
  id: number;
  scope: string;
  jalaliYear: number | null;
  jalaliMonth: number;
  jalaliDay: number;
  label: string;
};

export type NotificationItem = {
  id: number;
  title: string;
  body: string;
  read: boolean;
  createdAt: string;
  requestId?: number;
  kind?: string;
  category?: string;
};

export type Employee = {
  id: number;
  userId: number;
  displayName: string;
  firstName?: string;
  lastName?: string;
  email?: string;
  personnelCode: string;
  companyId: number;
  companyName: string;
  siteId?: number | null;
  siteName?: string | null;
  departmentId?: number | null;
  departmentName: string;
  managerId?: number | null;
  positionName: string;
  managerName: string | null;
  startDate: string;
  endDate: string | null;
  isActive: boolean;
  accountStatus?: EmployeeAccountStatus;
};

export type EmployeeAccountStatus =
  | "created"
  | "invitation_pending"
  | "invitation_sent"
  | "invitation_expired"
  | "invitation_revoked"
  | "account_activated"
  | "account_suspended"
  | "account_offboarded";

export type EmployeeOnboardingDraft = {
  firstName: string;
  lastName: string;
  personnelCode: string;
  email: string;
  companyId: number;
  siteId?: number | null;
  departmentId?: number | null;
  managerId?: number | null;
  startDate: string;
  endDate?: string | null;
};

export type EmployeeOnboardingResult = Employee & {
  invitationStatus?: "sent" | "pending";
  invitationDelivery?: "sent" | "unavailable";
  invitationMessageFa?: string;
  developmentActivationUrl?: string;
};

export type InvitationResult = {
  status: "sent" | "pending";
  message_fa: string;
  developmentActivationUrl?: string;
};

export type Site = {
  id: number;
  companyId: number;
  code: string;
  name: string;
  isActive: boolean;
};

export type BradfordNote = {
  employeeId: number;
  body: string;
  updatedAt: string;
  authorName: string;
};

export type Company = { id: number; code: string; name: string; isActive: boolean };
export type Department = { id: number; companyId: number; code: string; name: string; parentId: number | null };

export type ContractPeriod = {
  id: number;
  employeeId: number;
  start: string;
  end: string | null;
  workDays: number[];
  contractPercent: number;
};

export type AuditRow = {
  id: number;
  actorName: string | null;
  action: string;
  entity: string;
  entityId: string;
  reason: string;
  result: string;
  createdAt: string;
};

export type JobRow = {
  id: number;
  job: string;
  started: string;
  finished: string | null;
  status: string;
  error: string;
};

export type SettingItem = {
  key: string;
  value: unknown;
  default: unknown;
  labelFa: string;
  group: string;
};

export type CoverageRow = {
  date: string;
  departmentName: string;
  awayCount: number;
  headcount: number;
};

export type ReportSummary = {
  title: string;
  note?: string;
  series: { label: string; value: number }[];
};

export type ImportPreview = {
  dryRun: boolean;
  rows: { line: number; employee: string; type: string; year: number; days: string; ok: boolean; error?: string }[];
  accepted: number;
  rejected: number;
};

export type Page<T> = { results: T[]; count: number; page: number; pageSize: number };
