# API Contract

## 1. Scope and Status
This contract reflects the current state of the actual backend and frontend integration and is intentionally limited to the implemented or planned public API surface.

## 2. Base URL
- `/api/v1/`

## 3. API Inventory

### `GET /api/v1/health/`
- Status: Implemented
- Permission: AllowAny
- Response: JSON status object
- Success body example:
  ```json
  {
    "status": "ok",
    "service": "zsh-leave",
    "timestamp": "2026-11-01T00:00:00+00:00"
  }
  ```
- Error responses: none beyond server-level failures

### `GET /api/v1/ready/`
- Status: Implemented
- Permission: AllowAny
- Response: JSON readiness object with database, cache, and optional scheduler checks. The cache check performs a short-lived set/get/delete probe.
- Success body example:
  ```json
  {
    "status": "ready",
    "checks": [
      {"name": "database", "ok": true, "message": "database reachable"},
      {"name": "cache", "ok": true, "message": "cache operations succeeded"},
      {"name": "scheduler", "ok": true, "status": "not_required", "message": "scheduler is not required in this environment"}
    ],
    "timestamp": "2026-11-01T00:00:00+00:00"
  }
  ```
- Failure status: HTTP 503 with a non-sensitive message if infra dependencies are unavailable

### Authentication and account lifecycle

These same-origin endpoints use Django session cookies and match the frontend HTTP client paths (no trailing slash).

| Method | Endpoint | Contract |
|---|---|---|
| POST | `/api/v1/auth/login` | Accepts `{ "username": "...", "password": "..." }`; returns the authenticated frontend `User` shape. Invalid credentials and inactive accounts share a generic 401 response. Login is CSRF-protected and limited to 5 attempts/minute per client IP. |
| POST | `/api/v1/auth/logout` | Requires an authenticated session and CSRF token; invalidates the session and returns 204. |
| GET | `/api/v1/auth/me` | Returns the authenticated frontend `User` shape, or 401 when there is no valid session. Also bootstraps/refreshes the CSRF cookie for the SPA. |
| POST | `/api/v1/auth/password` | Requires an authenticated session and CSRF token; accepts `{ "current": "...", "next": "..." }`, validates the new password, and returns 204. The session remains active with its key rotated. |
| POST | `/api/v1/auth/activate` | Public, rate-limited, CSRF/session-independent activation; accepts exactly `{ "token", "email", "password" }`. A valid one-time invitation token is bound to the invited email; Django password validators run, and success activates the account without creating a login session. Invalid, expired, revoked, consumed, and email-mismatched invitations share a generic error. |

Sessions expire after eight hours and are browser-session cookies. Session cookies are HttpOnly and SameSite=Lax; production cookies require HTTPS. Passwords are stored with Argon2, while existing PBKDF2 hashes remain verifiable and upgrade on successful authentication. Employee creation provisions an inactive account with an unusable password; completing invitation activation enables the account, after which the employee signs in through the normal login endpoint. Password recovery for an already activated account is not implemented.

The `roles` and organization fields in the user response are display/route metadata resolved from the backend authorization context. Actual API access is independently enforced server-side and never trusts frontend roles.

### RBAC administration

| Method | Endpoint | Contract |
|---|---|---|
| GET/POST | `/api/v1/admin/role-assignments/` | System-administrator only. Lists current assignments or creates one using `{ "userId", "role", "scope", "companyId?", "siteId?", "departmentId?", "employeeId?", "expiresAt?" }`. `expiresAt`, when provided, must be a future timezone-aware ISO-8601 timestamp. Expired and inactive assignments do not grant roles/scopes; grants, revocations, and automatic expiry are audited. |
| DELETE | `/api/v1/admin/role-assignments/{id}/` | System-administrator only. Soft-revokes an assignment and writes an audit event. Self-revocation is rejected; unknown/inactive IDs return 404. |

Grants and revocations are audited and take effect on the next request. Existing HR/Executive Django groups are migrated only when an employee/company mapping exists; group membership alone is not authorization.

### `GET /api/v1/calendar/convert-date/`
- Status: Implemented (Phase 06 foundation)
- Permission: AllowAny; stateless conversion with no organization data
- Query parameters:
  - `calendar`: `jalali` or `gregorian`
  - `date`: `YYYY/MM/DD` for Jalali (Persian digits accepted), or `YYYY-MM-DD` for Gregorian
- Success response:
  ```json
  {
    "gregorian": "2025-03-21",
    "jalali": "1404/01/01"
  }
  ```
- Invalid calendar/date: HTTP 400 with `code` and `message_fa`
- Jalali dates outside the supported conversion range are rejected; they are not silently approximated.

### Phase 06 operational calendar APIs

All data-bearing endpoints require the configured authenticated session. Every endpoint below applies server-side role and organization-scope checks; frontend route guards are not security controls.

| Method | Endpoint | Contract |
|---|---|---|
| GET | `/api/v1/calendar/events/?from=YYYY-MM-DD&to=YYYY-MM-DD&scope=me\|team\|org` | Calendar events; `me` is personal, `team` uses the request manager snapshot, and `org` requires HR capability and uses the effective union scope (including same-company manager scope for HR+Manager), bounded by tenant. |
| GET | `/api/v1/calendar/holidays/?from=YYYY-MM-DD&to=YYYY-MM-DD` | Active range holidays applicable to the authenticated employee's national, organization, company, and site scopes. |
| GET | `/api/v1/working-days/estimate?employee={id}&start=YYYY-MM-DD&end=YYYY-MM-DD` | Backend day-count estimate; employees may query themselves, managers their current direct reports, and HR only assigned scope. Other employee IDs return 404. |
| GET/POST | `/api/v1/calendar/work-patterns/` | Read effective patterns for own profile; create a validated pattern with system-configuration permission. |
| GET/POST | `/api/v1/calendar/policies/` | Read effective policy for own profile (optional `date`); create immutable policy version with system-configuration permission. |
| GET/POST | `/api/v1/calendar/holidays/manage/` | List or create range-based holidays; requires system-configuration permission. |
| PATCH/DELETE | `/api/v1/calendar/holidays/manage/{id}/` | Edit or logically deactivate; deactivation requires a reason and writes history/audit. Requires system-configuration permission. |
| GET | `/api/v1/calendar/holidays/manage/{id}/history/` | Holiday history; requires system-configuration permission. |
| GET | `/api/v1/admin/calendar/work-patterns/` | Work-pattern history; requires system-configuration permission. |
| GET | `/api/v1/admin/calendar/policies/` | Policy-version history; requires system-configuration permission. |

Dates in these contracts are Gregorian ISO dates. Jalali entry/display belongs to the presentation layer; backend remains authoritative for validation and workday calculations. Workday and policy configuration is required: missing configuration returns a conflict rather than silently using a guessed default.

### Phase D operational leave workflow

All leave-request endpoints require an authenticated session. Request visibility is limited to the owner, the request's manager snapshot, or HR with matching organization scope; system administrators and executive viewers do not gain individual-record access. Mutations require an `Idempotency-Key` header (1–128 characters). Reusing a key with the same actor, operation, and payload returns the original resource; reusing it for different input returns 409. Workflow state changes and their append-only event/audit records commit atomically.

Business-critical administrative/HR mutations use the centralized audit service.
`X-Correlation-ID` is validated and echoed; otherwise a server-generated ID is
used. Audit source is server-derived. Audit writes fail closed in the same
transaction as covered mutations. Scoped HR audit reads return summaries only;
before/after, free-text reason, correlation ID, and source are restricted to
global administrators. See
[the coverage matrix](../remediation/AUDIT-COVERAGE-MATRIX.md) for audited
paths and unsupported mutation channels.

| Method | Endpoint | Contract |
|---|---|---|
| GET/POST | `/api/v1/leave-requests/` | List the effective union of owner, same-company manager-snapshot, and active HR-assignment requests. `scope=me\|team` narrows the list; it does not expand authorization. Supports optional `status`, `q`, `page`, and `page_size` (maximum 100), or submit using `employeeId?`, `leaveTypeKey`, `start`, `end`, `reason?`, and `replacementId?`. Workdays are calculated by the backend; a supplied `workingDays` must match. |
| GET/PATCH | `/api/v1/leave-requests/{id}/` | Read a scoped request or correct a `PENDING` request. Correction accepts `leaveTypeKey?`, `start`, `end`, `reason?`, and `replacementId?`; owner or in-scope HR only. |
| POST | `/api/v1/leave-requests/{id}/approve`, `/reject`, `/cancel`, `/withdraw` | Apply the corresponding domain transition. Rejection requires a reason. `withdraw` requests cancellation of an approved leave and requires manager/HR decision via `approve_withdraw` or `reject_withdraw`. |
| POST | `/api/v1/leave-requests/{id}/amend` | Create a correction request for an approved leave. The body may provide `start`, `end`, `reason`, and `replacementId`; omitted fields retain the original value. Approval cancels the original request in the same transaction. The existing frontend `/amend` action submits the unchanged values as a compatibility path. |
| GET/POST | `/api/v1/leave-requests/{id}/comments/` | List/add comments for request participants. Confidential request comments are restricted to in-scope HR. Comment bodies are append-only and limited to 4,000 characters. |
| GET | `/api/v1/leave-requests/{id}/events/` | Read append-only request event history. Confidential event reasons are only returned to in-scope HR. |

Out-of-scope request IDs return 404. Invalid transitions and idempotency conflicts return 409, permission failures 403, validation failures 422, and malformed payloads 400. Each transition/correction/comment records its actor and action; sensitive reasons are redacted from audit snapshots for confidential leave.

The legacy `/api/v1/holidays/` API remains available for one-day Jalali `PublicHoliday` entries and uses the same superuser-only write boundary. Its DELETE operation is logical deactivation and requires a reason.

### Phase F frontend integration APIs

These endpoints back the remaining frontend pages. They require an authenticated session unless noted. Role and organization scope are checked by the backend; the frontend's role guards are not authorization controls.

| Method | Endpoint | Contract |
|---|---|---|
| GET | `/api/v1/leave-types/` | Requires authentication and `leave_type.read`: Admin reads all types (including disabled); active scoped HR reads enabled types; Employee/Manager reads enabled, employee-requestable types. Exec-only and other authenticated roles are denied. HR scopes do not filter this global catalog because LeaveType has no company ownership. |
| POST/PATCH | `/api/v1/leave-types/`, `/api/v1/leave-types/{id}/` | Only global Admin with `leave_type.global.manage`; HR, Manager, Employee, Exec, and Anonymous are denied. Every create/update/activation/deactivation requires a trimmed `reason` of 1–500 characters. Key must match `^[a-z][a-z0-9_]{1,31}$`; `labelFa` is 1–128 characters; other fields use the LeaveType model's validation. Invalid input returns 400, denied access 403, duplicate keys or semantic updates after historical use 409, and throttling 429. Changes are transactional and audited with actor, operation, allowlisted before/after snapshots, reason, timestamp, and optional correlation ID. |
| DELETE | `/api/v1/leave-types/{id}/` | Unsupported (405). Use `PATCH {"enabled": false, "reason": "..."}` to deactivate. Deactivated types are rejected for new requests; existing requests and entitlement/ledger references are retained. Key and semantic fields cannot be changed after a LeaveRequest or LeaveEntitlement references the type, preserving historical labels and meaning. |
| GET | `/api/v1/balances/?year={year}&employee={id?}` | Return ledger-derived balance projections for self, same-company direct reports, or active HR-assignment scope (union). Confidential balance types remain visible only for HR-assigned employees. Reconciliation failures return 409. |
| GET | `/api/v1/entitlements/?year={year}` | List HR-authorized entitlements within the effective employee union; confidential values remain HR-assignment-gated. |
| POST | `/api/v1/entitlements/{id}/adjust` | Append a ledger adjustment; `{days, reason}` is required. |
| POST | `/api/v1/entitlements/import/?dry_run=0\|1` | Validate CSV (`personnel_code,type,year,days`). Commit is all-or-nothing and uses the entitlement ledger. |
| POST | `/api/v1/hr/absences/` | Create an absence for an employee within the caller's effective HR employee union. HR-originated requests enter `APPROVED` directly through `submit_request`; exactly one `submit` event records the transition from empty to approved. `Idempotency-Key` is required; the payload fingerprint covers employee, type, dates, working days, reason, and replacement. The request, event, audit entry, ledger posting, and idempotency record commit atomically. |
| GET | `/api/v1/employees/`, `/api/v1/companies/`, `/api/v1/departments/`, `/api/v1/sites/`, `/api/v1/sites/{id}/` | Read employee/organization rows in the effective union of self, same-company direct reports, and active HR assignments; Admin global access is endpoint-specific. Employee search accepts `q`; employee payloads include `siteId` and `siteName`. Site detail returns 404 outside the caller's organization scope. |
| POST/PATCH | `/api/v1/employees/`, `/api/v1/employees/{id}/` | HR/admin capability is required. Existing records use the effective employee union; new records require an HR destination assignment or an HR+Manager caller assigning the employee to their own same-company team. Creation accepts `firstName` + `lastName` (or legacy `displayName`), `personnelCode`, `companyId`, optional `email`, `departmentId`, `siteId`, `managerId`, `startDate`, and `endDate`. Duplicate email/personnel code returns 409. An email causes the backend to create and send a one-time invitation; without email the account stays inactive with an unusable password. |
| POST | `/api/v1/employees/{id}/invitation/` | HR/admin in scope; issues or resends an invitation, revoking an earlier active token. Optional `{ "email" }` can update the target email after uniqueness checks. HTTP 200 means the email backend accepted delivery; HTTP 503 means the invitation is pending and requires HR follow-up. A development activation URL is returned only under the explicit DEBUG-only development setting. |
| POST | `/api/v1/employees/{id}/invitation/revoke` | HR/admin in scope; requires `{ "reason" }` (non-empty, at most 500 characters); revokes active invitation tokens and audits the reason. |
| POST | `/api/v1/employees/{id}/suspend` | HR/admin in scope; requires `{ "reason" }`; disables account login, revokes active invitations, invalidates matching database-backed sessions, and audits the reason. |
| POST/PATCH | `/api/v1/sites/`, `/api/v1/sites/{id}/` | Global admins and HR assigned to the owning company can create/update Sites only within that company. Site ownership is immutable. `isActive: false` archives the Site without removing historical references. Mutations are audited atomically. |
| DELETE | `/api/v1/sites/{id}/` | Global admins or owning-company HR may delete only an unreferenced Site. Protected references return 409; use deactivation to preserve history. |
| POST/PATCH | `/api/v1/companies/`, `/api/v1/companies/{id}/`, `/api/v1/departments/`, `/api/v1/departments/{id}/` | Global-admin create/update; before/after allowlisted snapshots and AuditEvent commit atomically. |
| GET/POST | `/api/v1/employees/{id}/contract-periods/` | Read/add validated employee work-contract periods; HR capability is required and the employee must be in the effective employee union. |
| GET | `/api/v1/coverage/?from={date}&to={date}` | Aggregate approved absences per department/day for the caller's HR/Manager effective union; maximum 93 days. |
| GET | `/api/v1/reports/{kind}` | Return aggregate series for `absences`, `balances`, `trends`, or `process-health`; HR callers use the effective HR/Manager union, Exec uses assigned aggregates, and small groups are suppressed using `privacy.min_group_size`. `bradford` returns provisional employee-level scores to HR over the effective employee union, excluding confidential leave types; details are documented in [Bradford policy](../bradford-policy-fa.md). |
| GET | `/api/v1/exports/{dataset}.{csv\|xlsx}` | Authenticated bounded export. Datasets: `employees`, `leave-requests`, `employee-balances`, `absences`, `balances`, `trends`, `process-health`, and `bradford`. Uses the same server-side queryset/role scope as the corresponding employee, leave-request, balance, or report API; executive report outputs remain aggregate and k-suppressed. Confidential requests use the existing redacting serializer before file serialization. |
| GET/POST | `/api/v1/admin/settings/` | Read/validate/audit schema-defined settings; system-admin only. |
| GET | `/api/v1/admin/jobs/` | System-admin only; returns scheduler configuration/heartbeat plus job schedule, enabled state, and latest persisted execution (safe summary, attempt count, correlation ID, and error code). A disabled/missing scheduler is reported as `scheduler_not_configured`; this is not a manual-run endpoint. |
| GET | `/api/v1/audit/events/` | Return recent complete rows for global admins or redacted employee-related event summaries within current HR scope; other actors are denied. |
| GET | `/api/v1/policy/client` | Return actual employee-specific calendar work-pattern values. Hourly leave and auto-cancel remain disabled until backend policy support exists. |
| GET/POST | `/api/v1/hr/bradford-notes/` | Read/upsert notes for employees in HR scope. |
| GET/POST | `/api/v1/leave-requests/{id}/attachments/` | List or upload a PDF/PNG/JPEG file up to 10 MiB for the owner, HR in assignment, or the same-company manager of a non-confidential request. Content is structurally parsed; generated storage names do not use the submitted filename. Upload returns 201 only for `scanStatus=clean`, otherwise 202 with one of `pending_scan`, `scanning`, `rejected`, `scanner_unavailable`, or `scan_failed`. |
| GET | `/api/v1/leave-requests/{id}/attachments/{attachmentId}/` | Download only an active `clean` attachment for the owner, HR in assignment, or same-company manager of a non-confidential request. Other scan states and deleted files return 404; confidential files remain HR-assignment-only except for the owner. Attachment IDs are request-scoped and responses use attachment disposition, `nosniff`, sandbox CSP and private/no-store headers. |
| GET | `/api/v1/ready/` | Frontend health check; the HTTP client maps readiness and database checks into the UI health shape. |

Exports require a current authenticated request; no export file or bearer download token is persisted. `employee` filters are accepted only on employee-directory and balance exports and are applied inside the caller's authorized queryset. Company/tenant filters are not accepted. Request exports support the read API's `scope`, `status`, and `q` filters plus an optional overlapping `from`/`to` range of at most 366 days. The report exports reuse the report API's aggregate/suppression logic; Bradford reuses its HR scope, maximum 366-day window, and page limit.

CSV uses UTF-8 with BOM and CRLF, and both formats neutralize formula-like text. Filenames are generated by the server, content types are format-specific, and downloads set `private, no-store`, `Pragma: no-cache`, and `X-Content-Type-Options: nosniff`. CSV is streamed with a 10,000-row cap; XLSX is built in write-only mode with a 5,000-row cap. Larger output is rejected with HTTP 413. Every successful export writes an append-only `data.exported` AuditEvent with actor, dataset, format, authorized scope, non-sensitive filters, row count, timestamp, and correlation ID; row values and raw search text are excluded.

| Dataset | Employee | Manager | HR | Executive | System administrator |
|---|---|---|---|---|---|
| `employees` | Self | Direct team (or `scope=me`) | Assigned employee scope | Denied | Global employee directory |
| `leave-requests` | Own requests | Direct team (`scope=team`) | Assigned leave-request scope | Denied | Denied |
| `employee-balances` | Self | One employee already visible to the balance API | One employee in assigned HR scope | Denied | Denied |
| `absences`, `balances`, `trends`, `process-health` | Denied | Denied | Assigned-scope aggregate | Assigned-scope aggregate with minimum-group suppression | Denied |
| `bradford` | Denied | Denied | Assigned-scope, paginated | Denied | Denied |

Employee-directory access is not permission to export individual leave records: system administrators remain separated from leave data, consistent with the read APIs.
For multi-role accounts, effective access is the same union of scopes as the corresponding API, bounded by Company/Tenant. Combining roles does not create an endpoint capability or reveal confidential fields outside HR assignment. Where the read API returns an empty queryset rather than a permission error, the export returns a header-only file with `rowCount: 0`; no rows are disclosed. The normative policy and endpoint matrix are in [Authorization and Multi-Role Data Scope Policy](../authorization-policy.md).

### Schema endpoints
- `GET /api/v1/schema/` — Implemented via drf-spectacular
- `GET /api/v1/docs/` — Implemented via Swagger UI

## 4. Planned but Unimplemented
- Frontend management screens for range holidays, work patterns, and policy versions
- backend-authoritative hourly leave calculations tied to work patterns and holidays
- leave-year configuration, rollover, expiry, and their ledger operations
- self-service password recovery for activated accounts

## 5. Notes
- Pagination, filtering, and idempotency are enforced on the Phase D leave-request API, not globally on unrelated API resources.
- The contract is intentionally conservative and aligned with the current frontend surface rather than introducing unsupported domain commitments.
