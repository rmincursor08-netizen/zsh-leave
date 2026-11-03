# Authorization and Multi-Role Data Scope Policy

**Status:** Approved for F-04 implementation on 2026-11-03 (Option A: Union).
This document supersedes older, conflicting descriptions of multi-role access.
The backend authorization helpers are the implementation reference.

## Policy

An authenticated user's effective data scope is the union of the scopes granted
by their current roles, bounded by the associated company/tenant:

- **Employee:** their own active employee record and own leave requests.
- **Manager:** current direct reports in the manager's company. Existing leave
  requests remain associated with their recorded manager snapshot, and that
  snapshot never crosses the employee's company boundary.
- **HR:** the union of active, unexpired `RoleAssignment` scopes (company,
  site, department, or employee). A role assignment never implies global
  access.
- **Executive:** only explicitly assigned aggregate-report scopes.
- **System administrator:** global organization/configuration operations where
  the endpoint explicitly grants them. Admin does not imply access to
  individual leave requests.

Combining roles unions their applicable scopes; it does not manufacture an
endpoint capability that none of the roles has. If an endpoint requires HR,
manager, or admin capability, that check remains in force. A caller who has
that capability may use the union scope where the endpoint supports the
corresponding operation. Explicit filters such as `scope=me` or `scope=team`
may narrow, but never expand, the effective scope.

## Confidential data and tenant boundaries

- Cross-company access is never inferred from role combination. It is allowed
  only when an active HR assignment explicitly covers that company, or where
  an endpoint explicitly grants an admin organization operation.
- Manager scope alone does not reveal confidential leave type/reason,
  confidential comments/events, or confidential attachments. HR assignment
  scope is required for HR-only confidential details; an owner may access their
  own attachments under the attachment policy.
- Aggregate reports preserve confidentiality filters and minimum-group
  suppression. Executive views remain aggregate-only.
- Out-of-scope individual records are selected through scoped querysets and
  return 404 where object existence should not be disclosed.

## Endpoint scope matrix

| API family | Required capability | Effective row scope | Sensitive fields | Export/download |
|---|---|---|---|---|
| Employees and organization directory | Authenticated read; HR/admin for employee mutation | Self + same-company direct reports + active HR assignments; Admin global only where explicitly allowed | Employee profile data limited to this scope | Employee export uses the same scoped queryset |
| Leave requests, comments, events, notifications | Employee, Manager, or HR capability as applicable | Owner + same-company manager snapshot + active HR-assignment requests | Confidential details only for owner policy or HR assignment; manager data is redacted | Request export and request-scoped attachments use the corresponding authorized scope |
| Balances | Authenticated read | Self + same-company direct reports + active HR assignments | Confidential balance types are visible only when the viewer has HR assignment for that employee | Balance export uses the same row and field visibility |
| LeaveType catalog | `leave_type.read` for Admin, scoped HR, Employee, or Manager; `leave_type.global.manage` for writes | Admin: all; HR: enabled; Employee/Manager: enabled and requestable. The catalog is global and has no company filter. | Exec-only and anonymous callers are denied | No catalog export |
| Entitlements, adjustment, import, HR absence, contracts, Bradford notes | HR or explicitly permitted Admin operation | HR operation uses the effective employee union; Admin is global only where specified | Confidential fields remain assignment-gated | No separate export privilege |
| Reports and coverage | HR/Executive or Manager according to endpoint | HR-capable callers use the effective employee/request union; Manager-only coverage uses direct reports; Exec uses assigned aggregates | Confidential requests outside HR assignment are excluded; aggregate suppression remains | Report exports reuse the same filtered response |
| Bradford | HR capability | Effective employee scope, including manager/self scope for HR+Manager; calculation excludes confidential leave types | Individual score fields are limited to authorized employee scope | Paginated API/export reuse this scope |
| Attachments | Owner, HR in assignment, or same-company Manager for non-confidential requests | Owner + active HR assignment + direct manager snapshot for non-confidential requests | Manager cannot list/upload/download confidential attachments | Only active `clean` files may be downloaded; the same authorization is applied before scan-state checks |
| Calendar | `me`, `team`, or HR `org` capability | Explicit parameter narrows the effective scope; `org` includes the HR+Manager union | Labels remain neutral for non-owner/confidential records | No calendar export |
| Audit events | Admin or HR | Admin global; HR uses effective employee scope | HR receives redacted summaries; admin-only fields remain restricted | Reads are not downloadable exports |

Exports log the effective role/scope description, filters, format, row count,
actor, and correlation ID. Mutations continue to audit the actor and affected
record. Role grants, revocations, and automatic expiry are audited. Routine
API reads are not individually persisted as audit events.

## Role assignment lifecycle

`RoleAssignment.is_active=False` revokes the grant. Optional `expires_at` is an
aware ISO-8601 timestamp; the assignment is effective only while
`expires_at IS NULL OR expires_at > now`. Expired or inactive assignments do
not contribute a role or scope. The role-management endpoint returns current
assignments only. Expired rows are marked inactive and audited when a new
assignment is granted to that user, allowing the existing uniqueness
constraints to remain safe.

Manager scope is derived from the current employee hierarchy for direct
reports; historical requests use the manager snapshot recorded on that
request. Ended or unactivated employee profiles do not contribute employee or
manager roles.

## Implementation and verification

Central scope logic is in `backend/leave/authorization.py`; endpoint-specific
capability checks remain in the API views. Session-authenticated API tests
with CSRF checks cover multi-role read, write, export, download, confidential
attachment denial, cross-company boundaries, and expired/revoked assignments.
SQLite results are application-level evidence only, not PostgreSQL proof.
