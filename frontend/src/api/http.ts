import { ApiError, UNAUTHORIZED_EVENT } from "./errors";
import type { Api, LeaveDraft, RequestFilters } from "./contract";
import type { ApiErrorBody } from "./types";

function csrf(): string {
  const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

function idempotencyKey(): string {
  return crypto.randomUUID();
}

/** پیام فارسی پیش‌فرض بر اساس کد وضعیت، وقتی بدنه خطا از سرور قابل‌خواندن نباشد. */
function fallbackMessage(status: number): string {
  switch (status) {
    case 400: return "درخواست نامعتبر است";
    case 401: return "نشست شما معتبر نیست؛ دوباره وارد شوید";
    case 403: return "دسترسی لازم را ندارید";
    case 404: return "مورد درخواستی یافت نشد";
    case 409: return "درخواست با وضعیت فعلی تعارض دارد";
    case 422: return "اطلاعات واردشده معتبر نیست";
    case 429: return "تعداد درخواست‌ها زیاد است؛ کمی بعد دوباره تلاش کنید";
    default: return status >= 500 ? "خطای سرور؛ کمی بعد دوباره تلاش کنید" : "خطای ناشناخته";
  }
}

function isErrorBody(v: unknown): v is Partial<ApiErrorBody> {
  return typeof v === "object" && v !== null;
}

/** مسیرهایی که 401 آن‌ها «نشست منقضی‌شده» نیست و نباید رویداد خروج اجباری بفرستند. */
const AUTH_PATHS = new Set(["/auth/login", "/auth/me"]);

async function req<T>(
  path: string,
  init: RequestInit = {},
  acceptedStatuses: number[] = [],
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  headers.set("Accept", "application/json");
  const token = csrf();
  if (token) headers.set("X-CSRFToken", token);

  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, { credentials: "include", ...init, headers });
  } catch {
    throw new ApiError(0, { code: "network", message_fa: "ارتباط با سرور برقرار نشد؛ اتصال شبکه را بررسی کنید" });
  }

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  let data: unknown = null;
  let parsed = true;
  if (text) {
    try {
      data = JSON.parse(text) as unknown;
    } catch {
      parsed = false;
    }
  }

  if (!res.ok && !acceptedStatuses.includes(res.status)) {
    if (res.status === 401 && !AUTH_PATHS.has(path.split("?")[0])) {
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    }
    const body: Partial<ApiErrorBody> = parsed && isErrorBody(data) ? data : {};
    throw new ApiError(res.status, {
      code: body.code ?? "http",
      message_fa: body.message_fa ?? fallbackMessage(res.status),
      fields: body.fields,
    });
  }

  if (!parsed) throw new ApiError(res.status, { code: "bad_response", message_fa: "پاسخ نامعتبر از سرور دریافت شد" });
  return data as T;
}

export const httpApi: Api = {
  login: (username, password) => req("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => req("/auth/logout", { method: "POST" }),
  me: () => req("/auth/me"),
  changePassword: (current, next) => req("/auth/password", { method: "POST", body: JSON.stringify({ current, next }) }),
  activateAccount: (token, email, password) => req("/auth/activate", {
    method: "POST",
    body: JSON.stringify({ token, email, password }),
  }),
  listTypes: () => req("/leave-types/"),
  saveType: (t) => {
    const { id, ...body } = t;
    return req(id ? `/leave-types/${id}/` : "/leave-types/", {
      method: id ? "PATCH" : "POST", body: JSON.stringify({ ...body, reason: t.reason.trim() }),
    });
  },
  listRequests: (f: RequestFilters) => {
    const q = new URLSearchParams();
    if (f.page) q.set("page", String(f.page));
    if (f.status) q.set("status", f.status);
    if (f.q) q.set("q", f.q);
    if (f.mine) q.set("scope", "me");
    if (f.team) q.set("scope", "team");
    const qs = q.toString();
    return req(`/leave-requests/${qs ? `?${qs}` : ""}`);
  },
  getRequest: (id) => req(`/leave-requests/${id}/`),
  createRequest: (d) => req("/leave-requests/", {
    method: "POST", headers: { "Idempotency-Key": idempotencyKey() }, body: JSON.stringify(d),
  }),
  updateRequest: (id, d) => req(`/leave-requests/${id}/`, {
    method: "PATCH", headers: { "Idempotency-Key": idempotencyKey() }, body: JSON.stringify(d),
  }),
  actRequest: (id, action, reason) => req(`/leave-requests/${id}/${action}`, {
    method: "POST", headers: { "Idempotency-Key": idempotencyKey() }, body: JSON.stringify({ reason }),
  }),
  listComments: (id) => req(`/leave-requests/${id}/comments/`),
  addComment: (id, body) => req(`/leave-requests/${id}/comments/`, {
    method: "POST", headers: { "Idempotency-Key": idempotencyKey() }, body: JSON.stringify({ body }),
  }),
  listAttachments: (id) => req(`/leave-requests/${id}/attachments/`),
  addAttachment: (id, file) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return req(`/leave-requests/${id}/attachments/`, {
      method: "POST", body: form,
    });
  },
  balances: (year, employeeId) => req(`/balances/?year=${year}${employeeId ? `&employee=${employeeId}` : ""}`),
  entitlements: (year) => req(`/entitlements/?year=${year}`),
  adjustEntitlement: (id, days, reason) => req(`/entitlements/${id}/adjust`, {
    method: "POST", headers: { "Idempotency-Key": idempotencyKey() }, body: JSON.stringify({ days, reason }),
  }),
  importEntitlements: (csv, dryRun) => req(`/entitlements/import/?dry_run=${dryRun ? "1" : "0"}`, { method: "POST", body: JSON.stringify({ csv }) }),
  recordAbsence: (d: LeaveDraft) => req("/hr/absences/", {
    method: "POST", headers: { "Idempotency-Key": idempotencyKey() }, body: JSON.stringify(d),
  }),
  calendar: (from, to, scope) => req(`/calendar/events/?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&scope=${scope}`),
  calendarHolidays: (from, to) => req(`/calendar/holidays/?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`),
  holidays: () => req("/holidays/"),
  saveHoliday: (h) => req(h.id ? `/holidays/${h.id}/` : "/holidays/", { method: h.id ? "PATCH" : "POST", body: JSON.stringify(h) }),
  deleteHoliday: (id, reason) => req(`/holidays/${id}/`, { method: "DELETE", body: JSON.stringify({ reason }) }),
  notifications: () => req("/notifications/"),
  readNotification: (id) => req(`/notifications/${id}/read`, { method: "POST" }),
  employees: (q) => req(`/employees/${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  saveEmployee: (e) => req("/employees/", { method: "POST", body: JSON.stringify(e) }),
  inviteEmployee: (id, email) => req(
    `/employees/${id}/invitation/`,
    { method: "POST", body: JSON.stringify({ email }) },
    [503],
  ),
  revokeEmployeeInvitation: (id, reason) => req(
    `/employees/${id}/invitation/revoke`,
    { method: "POST", body: JSON.stringify({ reason }) },
  ),
  suspendEmployee: (id, reason) => req(
    `/employees/${id}/suspend`,
    { method: "POST", body: JSON.stringify({ reason }) },
  ),
  companies: () => req("/companies/"),
  departments: () => req("/departments/"),
  sites: () => req("/sites/"),
  saveCompany: (c) => {
    const { id, ...body } = c;
    return req(id ? `/companies/${id}/` : "/companies/", {
      method: id ? "PATCH" : "POST", body: JSON.stringify(body),
    });
  },
  saveDepartment: (d) => {
    const { id, ...body } = d;
    return req(id ? `/departments/${id}/` : "/departments/", {
      method: id ? "PATCH" : "POST", body: JSON.stringify(body),
    });
  },
  contracts: (employeeId) => req(`/employees/${employeeId}/contract-periods/`),
  saveContract: (p) => req(`/employees/${p.employeeId}/contract-periods/`, { method: "POST", body: JSON.stringify(p) }),
  coverage: (from, to) => req(`/coverage/?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`),
  reports: (kind) => req(`/reports/${encodeURIComponent(kind)}`),
  settings: () => req("/admin/settings/"),
  saveSetting: (key, value, reason) => req("/admin/settings/", { method: "POST", body: JSON.stringify({ key, value, reason }) }),
  jobs: () => req("/admin/jobs/"),
  audit: () => req("/audit/events/"),
  health: async () => {
    const response = await req<{
      status: string;
      checks: { name: string; ok: boolean; message: string }[];
    }>("/ready/");
    const database = response.checks.find((check) => check.name === "database");
    return {
      live: true,
      ready: response.status === "ready",
      db: database?.message ?? "نامشخص",
      lastJob: null,
    };
  },
  estimate: (employeeId, start, end) => req(`/working-days/estimate?employee=${employeeId ?? ""}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`),
  clientPolicy: () => req("/policy/client"),
  bradfordNotes: () => req("/hr/bradford-notes/"),
  saveBradfordNote: (employeeId, body) => req("/hr/bradford-notes/", { method: "POST", body: JSON.stringify({ employeeId, body }) }),
};
