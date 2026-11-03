import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, DEMO_MODE } from "../api";
import { asApiError, errorMessage } from "../api/errors";
import type { Attachment, LeaveRequest } from "../api/types";
import { JalaliDateField } from "../components/JalaliDateField";
import {
  Badge, Banner, Button, Card, ConfirmDialog, DataTable, DetailList, Empty, ErrorState, Field, Input,
  LoadingBlock, Modal, PageHeader, Pagination, SearchInput, Segmented, Select, StatusBadge, Textarea, type Column,
} from "../components/ui";
import { DeadlineLabel } from "../components/Deadline";
import { expiryDate } from "../lib/autoCancel";
import { deadlineInfo } from "../lib/deadline";
import { camelFields, positiveNumber } from "../lib/forms";
import { focusFirstInvalid, useDebounced } from "../lib/hooks";
import { holidayIsosForRange } from "../lib/holidayDates";
import {
  findDailyConflict, findHourlyConflict, formatDurationFa, minutesBetween, validateHourlyDraft,
  workingDaysFromMinutes, type DurationKind, type ExistingInterval,
} from "../lib/hourlyLeave";
import { durationKindLabel, durationKindOf, formatRequestDuration, formatRequestRange } from "../lib/leaveDisplay";
import { formatJalaliSafe, normalizeNumberInput, todayIso } from "../lib/jalali";
import { STATUS_LABEL, STATUS_ORDER, hasAny, isOpenStatus } from "../lib/roles";
import { useAsync } from "../lib/useAsync";
import { useAuth } from "../state/Auth";
import { useToast } from "../state/Toast";

const REQUEST_COLUMNS: Column<LeaveRequest>[] = [
  { key: "id", label: "شماره", render: (r) => <Link className="row-link" to={`/requests/${r.id}`}>{r.id}</Link> },
  { key: "employeeName", label: "کارمند" },
  { key: "type", label: "نوع", render: (r) => r.typeLabel ?? "غایب" },
  {
    key: "range", label: "بازه",
    render: (r) => (
      <>
        {formatRequestRange(r)}
        {durationKindOf(r) === "hourly" && <span className="kind-tag">ساعتی</span>}
      </>
    ),
  },
  { key: "workingDays", label: "مدت", render: (r) => formatRequestDuration(r) },
  { key: "status", label: "وضعیت", render: (r) => <StatusBadge status={r.status} /> },
  { key: "deadline", label: "مهلت تأیید", render: (r) => <DeadlineLabel info={deadlineInfo(r, todayIso())} /> },
];

const ATTACHMENT_SCAN_LABEL: Record<NonNullable<Attachment["scanStatus"]>, string> = {
  pending_scan: "در انتظار اسکن",
  scanning: "در حال اسکن",
  clean: "پاک‌سازی‌شده",
  rejected: "ردشده",
  scanner_unavailable: "اسکنر در دسترس نیست",
  scan_failed: "اسکن ناموفق",
  deleted: "حذف‌شده",
};

export function RequestsPage() {
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const dq = useDebounced(q.trim(), 300);
  const { data, error, loading, reload } = useAsync(
    () => api.listRequests({ page: page > 1 ? page : undefined, status: status || undefined, q: dq || undefined }),
    [status, dq, page],
  );
  const rows = data?.results ?? [];
  const filtered = !!status || !!dq;

  return (
    <>
      <PageHeader
        title="درخواست‌های مرخصی"
        subtitle="فهرست بر اساس دامنه دسترسی شما"
        actions={<Link className="btn sm" to="/requests/new">ثبت جدید</Link>}
      />
      <div className="toolbar" role="search">
        <SearchInput
          aria-label="جستجوی درخواست بر اساس نام، نوع یا شماره"
          placeholder="جستجو نام یا شماره"
          value={q}
          onChange={(e) => { setQ(e.target.value); setPage(1); }}
        />
        <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label="فیلتر وضعیت">
          <option value="">همه وضعیت‌ها</option>
          {STATUS_ORDER.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
        </select>
      </div>
      {error && <ErrorState message={error} onRetry={() => void reload()} />}
      {loading && !data ? <LoadingBlock /> : (
        <div aria-busy={loading}>
          <DataTable
            columns={REQUEST_COLUMNS}
            rows={rows}
            rowKey={(r) => r.id}
            caption="فهرست درخواست‌های مرخصی"
            empty={<Empty title="درخواستی یافت نشد" hint={filtered ? "فیلتر یا عبارت جستجو را تغییر دهید." : undefined} />}
          />
          {data ? <Pagination page={page} pageSize={data.pageSize} count={data.count} onChange={setPage} /> : null}
        </div>
      )}
    </>
  );
}

export function RequestNewPage() {
  const { user } = useAuth();
  const toast = useToast();
  const nav = useNavigate();
  const formRef = useRef<HTMLFormElement>(null);
  const { data: types, error: typesError, loading: typesLoading } = useAsync(() => api.listTypes(), []);
  const { data: employees, error: employeesError, reload: reloadEmployees } = useAsync(() => api.employees(), []);
  const { data: policy, error: policyError, reload: reloadPolicy } = useAsync(() => api.clientPolicy(), []);
  const { data: holidayRows, error: holidaysError, reload: reloadHolidays } = useAsync(() => api.holidays(), []);
  const { data: mine, error: mineError, reload: reloadMine } = useAsync(() => api.listRequests({ mine: true }), []);
  const requestable = (types ?? []).filter((t) => t.employeeRequestable && t.enabled);
  const [kind, setKind] = useState<DurationKind>("daily");
  const [startTime, setStartTime] = useState("10:00");
  const [endTime, setEndTime] = useState("12:00");
  const [leaveTypeKey, setType] = useState("annual");
  const [start, setStart] = useState(todayIso());
  const [end, setEnd] = useState(todayIso());
  const [workingDays, setDays] = useState("1.0");
  const [estimated, setEstimated] = useState(true);
  const [estimating, setEstimating] = useState(false);
  const [reason, setReason] = useState("");
  const [replacementId, setRep] = useState<number | "">("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);
  const t = requestable.find((x) => x.key === leaveTypeKey) ?? requestable[0];
  const employeeId = user?.employeeId ?? null;
  // مرخصی ساعتی فقط وقتی سیاست فعال و بارگذاری‌شده باشد؛ در غیر این صورت فرم همان روزانه‌ی قبلی است
  const hourlyAllowed = !!policy?.hourlyLeaveEnabled;
  const effKind: DurationKind = hourlyAllowed ? kind : "daily";
  const hourly = effKind === "hourly";
  const minutes = minutesBetween(startTime, endTime);
  const workdayMinutes = policy?.workdayMinutes ?? 0;
  const existing: ExistingInterval[] = (mine?.results ?? []).map((r) => ({
    id: r.id, employeeId: r.employeeId, status: r.status, start: r.start, end: r.end,
    durationKind: r.durationKind, startTime: r.startTime, endTime: r.endTime,
  }));

  // تخمین خودکار روز کاری تا زمانی که کاربر مقدار را دستی ننوشته است (فقط مرخصی روزانه)
  useEffect(() => {
    if (hourly || !estimated || !start || !end || start > end) return;
    let alive = true;
    api.estimate(employeeId, start, end)
      .then((e) => {
        if (alive) {
          setDays(Number(e.days).toFixed(1));
          setFormError("");
        }
      })
      .catch((err) => {
        if (alive) {
          setEstimated(false);
          setFormError(`محاسبه روزهای کاری انجام نشد: ${errorMessage(err)}`);
        }
      });
    return () => { alive = false; };
  }, [hourly, estimated, start, end, employeeId]);

  const estimate = async () => {
    setEstimating(true);
    setFormError("");
    try {
      const e = await api.estimate(employeeId, start, end);
      setDays(Number(e.days).toFixed(1));
      setEstimated(true);
      setErrors((x) => ({ ...x, workingDays: "" }));
    } catch (err) {
      setFormError(errorMessage(err));
    } finally {
      setEstimating(false);
    }
  };

  const validate = (): Record<string, string> => {
    const e: Record<string, string> = {};
    if (!t) e.leaveTypeKey = "نوع مرخصی را انتخاب کنید";
    if (hourly) {
      // الگوی روز کاری را Backend/تخمین تعیین می‌کند؛ اینجا همه‌ی روزها مجاز است و روز کاری در submit با estimate کنترل می‌شود
      const herr = validateHourlyDraft(
        { date: start, startTime, endTime },
        {
          workdayStart: policy?.workdayStart ?? "",
          workdayEnd: policy?.workdayEnd ?? "",
          allowOutsideWorkHours: policy?.hourlyAllowOutsideWorkHours ?? false,
          allowOnHoliday: policy?.hourlyAllowOnHoliday ?? false,
          maxMinutesPerRequest: policy?.hourlyMaxMinutesPerRequest ?? 0,
          workWeekdays: [0, 1, 2, 3, 4, 5, 6],
        },
        holidayIsosForRange(holidayRows ?? [], start || todayIso(), start || todayIso()),
      );
      if (herr.date) e.start = herr.date;
      if (herr.startTime) e.startTime = herr.startTime;
      if (herr.endTime) e.endTime = herr.endTime;
      if (!Object.keys(e).length && employeeId != null && findHourlyConflict(employeeId, { date: start, startTime, endTime }, existing)) {
        e.startTime = "این بازه ساعتی با یکی از درخواست‌های فعال شما تداخل دارد";
      }
    } else {
      if (!start) e.start = "تاریخ شروع را وارد کنید";
      if (!end) e.end = "تاریخ پایان را وارد کنید";
      if (start && end && start > end) e.end = "تاریخ پایان نباید قبل از تاریخ شروع باشد";
      if (positiveNumber(workingDays) === null) e.workingDays = "روز کاری باید عددی بزرگ‌تر از صفر باشد";
      if (!e.start && !e.end && employeeId != null && findDailyConflict(employeeId, start, end, existing)) {
        e.end = "این بازه با یکی از درخواست‌های فعال شما تداخل دارد";
      }
    }
    if (t?.requiresNote && !reason.trim()) e.reason = "توضیح برای این نوع الزامی است";
    if (t?.requiresReplacement && replacementId === "") e.replacementId = "انتخاب جانشین برای این نوع الزامی است";
    return e;
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const found = validate();
    setErrors(found);
    setFormError("");
    if (Object.keys(found).length || !t) {
      focusFirstInvalid(formRef.current);
      return;
    }
    setBusy(true);
    try {
      if (hourly) {
        // روز کاری بودن تاریخ (الگوی قرارداد + تعطیلات) از همان تخمین Backend گرفته می‌شود
        const holidayToday = holidayIsosForRange(holidayRows ?? [], start, start).includes(start);
        const est = await api.estimate(employeeId, start, start);
        if (Number(est.days) === 0 && !(holidayToday && policy?.hourlyAllowOnHoliday)) {
          setErrors({ start: "این تاریخ طبق الگوی کاری شما روز کاری نیست" });
          focusFirstInvalid(formRef.current);
          return;
        }
      }
      const base = {
        leaveTypeKey: t.key, reason: reason.trim(),
        replacementId: replacementId === "" ? null : Number(replacementId),
      };
      const row = await api.createRequest(
        hourly && minutes != null
          ? {
            ...base, start, end: start, durationKind: "hourly", startTime, endTime, durationMinutes: minutes,
            workingDays: workingDaysFromMinutes(minutes, workdayMinutes),
          }
          : { ...base, start, end, workingDays: normalizeNumberInput(workingDays) },
      );
      toast("درخواست ثبت شد", "success");
      nav(`/requests/${row.id}`);
    } catch (err) {
      const ae = asApiError(err);
      setFormError(ae.message);
      setErrors(camelFields(ae.fields));
      focusFirstInvalid(formRef.current);
    } finally {
      setBusy(false);
    }
  };

  const noWorkingDays = !hourly && estimated && start <= end && positiveNumber(workingDays) === null;

  return (
    <>
      <PageHeader title="ثبت درخواست مرخصی" subtitle="مقدار روز کاری یک تخمین است تا زمانی که شما تأیید کنید" />
      {typesError && <ErrorState message={typesError} />}
      {employeesError && <ErrorState message={employeesError} onRetry={() => void reloadEmployees()} />}
      {policyError && <ErrorState message={policyError} onRetry={() => void reloadPolicy()} />}
      {holidaysError && <ErrorState message={holidaysError} onRetry={() => void reloadHolidays()} />}
      {mineError && <ErrorState message={mineError} onRetry={() => void reloadMine()} />}
      {formError && <Banner tone="danger">{formError}</Banner>}
      <Card>
        {typesLoading && !types ? <LoadingBlock /> : (
          <form onSubmit={submit} ref={formRef} noValidate>
            {hourlyAllowed && (
              <Segmented
                label="نوع مدت مرخصی"
                value={effKind}
                onChange={(v) => { setKind(v as DurationKind); setErrors({}); }}
                items={[{ id: "daily", label: "مرخصی روزانه" }, { id: "hourly", label: "مرخصی ساعتی" }]}
              />
            )}
            <div className="grid grid-2">
              <Field label="نوع مرخصی" required error={errors.leaveTypeKey}>
                <Select value={t?.key ?? ""} onChange={(e) => setType(e.target.value)}>
                  {requestable.map((x) => <option key={x.key} value={x.key}>{x.labelFa}</option>)}
                </Select>
              </Field>
              <Field
                label="جانشین"
                required={!!t?.requiresReplacement}
                error={errors.replacementId}
                hint={t?.requiresReplacement ? "برای این نوع الزامی است" : "اختیاری"}
              >
                <Select value={replacementId} onChange={(e) => setRep(e.target.value ? Number(e.target.value) : "")}>
                  <option value="">—</option>
                  {employees?.filter((emp) => emp.id !== employeeId && emp.isActive).map((emp) => (
                    <option key={emp.id} value={emp.id}>{emp.displayName}</option>
                  ))}
                </Select>
              </Field>
              <Field label={hourly ? "تاریخ" : "از تاریخ"} required error={errors.start}>
                <JalaliDateField value={start} onChange={(v) => { setStart(v); setEstimated(true); }} />
              </Field>
              {!hourly && (
                <Field label="تا تاریخ" required error={errors.end}>
                  <JalaliDateField value={end} min={start || undefined} onChange={(v) => { setEnd(v); setEstimated(true); }} />
                </Field>
              )}
            </div>
            {hourly && (
              <>
                <div className="time-row">
                  <Field label="ساعت شروع" required error={errors.startTime} hint={`ساعات کاری: ${policy?.workdayStart ?? "—"} تا ${policy?.workdayEnd ?? "—"}`}>
                    <Input type="time" step={300} value={startTime} dir="ltr" className="ltr-time" onChange={(e) => setStartTime(e.target.value)} />
                  </Field>
                  <Field label="ساعت پایان" required error={errors.endTime}>
                    <Input type="time" step={300} value={endTime} dir="ltr" className="ltr-time" onChange={(e) => setEndTime(e.target.value)} />
                  </Field>
                </div>
                <div className="duration-summary" aria-live="polite">
                  <span>مدت محاسبه‌شده:</span>
                  <strong>{minutes != null && minutes > 0 ? formatDurationFa(minutes) : "—"}</strong>
                  {minutes != null && minutes > 0 && (
                    <Badge tone="info">≈ {workingDaysFromMinutes(minutes, workdayMinutes)} روز کاری (تخمین)</Badge>
                  )}
                </div>
              </>
            )}
            {!hourly && <Field
              label="روز کاری"
              required
              error={errors.workingDays}
              hint={noWorkingDays ? "در این بازه روز کاری وجود ندارد؛ بازه را بررسی کنید." : "Backend هنگام ثبت، مقدار را دوباره با تقویم و سیاست مؤثر محاسبه می‌کند"}
            >
              <div className="toolbar">
                <Input
                  value={workingDays}
                  inputMode="decimal"
                  dir="ltr"
                  className="ltr-input"
                  onChange={(e) => { setDays(e.target.value); setEstimated(false); }}
                />
                <Button type="button" variant="secondary" loading={estimating} onClick={() => void estimate()}>تخمین</Button>
                {estimated && <Badge tone="info">تخمین</Badge>}
              </div>
            </Field>}
            <Field label="توضیح" required={!!t?.requiresNote} error={errors.reason} hint={t?.requiresNote ? "برای این نوع الزامی است" : undefined}>
              <Textarea value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
            {policy?.autoCancelEnabled && policy.autoCancelDays ? (
              <Banner tone="info">
                مهلت تأیید: اگر تا {policy.autoCancelDays} روز تقویمی (تا پیش از {formatJalaliSafe(expiryDate(todayIso(), policy.autoCancelDays), true)})
                تأیید یا رد نشود، درخواست به‌صورت خودکار لغو می‌شود.
              </Banner>
            ) : null}
            <div className="toolbar">
              <Button type="submit" loading={busy} disabled={!requestable.length}>ارسال درخواست</Button>
              <Link className="btn secondary" to="/requests">انصراف</Link>
            </div>
          </form>
        )}
      </Card>
    </>
  );
}

type PendingAction = { action: string; title: string; message: string; label: string; danger?: boolean };

export function RequestDetailPage() {
  const { id } = useParams();
  const rid = Number(id);
  const valid = Number.isInteger(rid) && rid > 0;
  const { user } = useAuth();
  const toast = useToast();
  const nav = useNavigate();
  const { data: row, error, loading, reload } = useAsync(
    () => (valid ? api.getRequest(rid) : Promise.reject(new Error("شناسه درخواست نامعتبر است"))),
    [rid],
  );
  // برای رکورد محرمانه، نظرها و پیوست‌ها فقط برای HR بارگذاری می‌شود (سیاست حریم خصوصی)
  const isHr = hasAny(user?.roles ?? [], "hr");
  const showExtras = !!row && !(row.confidential && !isHr);
  const { data: comments, error: commentsError, reload: rc } = useAsync(
    () => (showExtras ? api.listComments(rid) : Promise.resolve([])), [rid, showExtras],
  );
  const { data: files, error: filesError, reload: rf } = useAsync(
    () => (showExtras ? api.listAttachments(rid) : Promise.resolve([])), [rid, showExtras],
  );
  const fileInput = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [note, setNote] = useState("");
  const [noteError, setNoteError] = useState("");
  const [noteBusy, setNoteBusy] = useState(false);
  const [fileBusy, setFileBusy] = useState(false);
  const [rejectOpen, setReject] = useState(false);
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState("");
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const act = async (action: string, rsn?: string): Promise<boolean> => {
    setBusy(action);
    try {
      const res = await api.actRequest(rid, action, rsn);
      if (action === "amend" && res.id !== rid) {
        toast("درخواست اصلاحی ایجاد شد", "success");
        nav(`/requests/${res.id}`);
        return true;
      }
      toast("انجام شد", "success");
      await reload();
      return true;
    } catch (e) {
      toast(errorMessage(e), "danger");
      return false;
    } finally {
      setBusy(null);
    }
  };

  const confirmPending = async () => {
    if (!pending) return;
    await act(pending.action);
    setPending(null);
  };

  const submitReject = async () => {
    if (!reason.trim()) {
      setReasonError("درج دلیل رد الزامی است");
      return;
    }
    const ok = await act("reject", reason.trim());
    if (ok) {
      setReject(false);
      setReason("");
      setReasonError("");
    }
  };

  const addNote = async (e: FormEvent) => {
    e.preventDefault();
    const body = note.trim();
    if (!body) {
      setNoteError("متن نظر را بنویسید");
      return;
    }
    setNoteBusy(true);
    setNoteError("");
    try {
      await api.addComment(rid, body);
      setNote("");
      await rc();
    } catch (err) {
      setNoteError(errorMessage(err));
    } finally {
      setNoteBusy(false);
    }
  };

  const addFile = async () => {
    if (!selectedFile) return;
    setFileBusy(true);
    try {
      const uploaded = await api.addAttachment(rid, selectedFile);
      await rf();
      setSelectedFile(null);
      if (fileInput.current) fileInput.current.value = "";
      toast(
        DEMO_MODE
          ? "پیوست نمایشی افزوده شد"
          : uploaded.scanStatus === "clean"
            ? "پیوست بارگذاری و اسکن شد"
            : "پیوست ثبت شد؛ تا پایان اسکن قابل دانلود نیست",
        "success",
      );
    } catch (err) {
      toast(errorMessage(err), "danger");
    } finally {
      setFileBusy(false);
    }
  };

  if (error) return <ErrorState message={error} onRetry={valid ? () => void reload() : undefined} />;
  if (!row || !user) return loading ? <LoadingBlock /> : null;
  const own = row.employeeId === user.employeeId;
  const open = isOpenStatus(row.status);
  const deadline = deadlineInfo(row, todayIso());
  const confidentialMasked = !!row.confidential && !isHr;

  return (
    <>
      <PageHeader
        title={`درخواست ${row.id}`}
        subtitle={`${row.employeeName} · ${row.departmentName}`}
        actions={(
          <>
            <StatusBadge status={row.status} />
            <Link to="/requests" className="btn ghost sm">بازگشت به فهرست</Link>
          </>
        )}
      />
      {confidentialMasked && <Banner tone="info">جزئیات این رکورد محرمانه است؛ فقط تاریخ و وضعیت نمایش داده می‌شود.</Banner>}
      <div className="grid grid-2">
        <Card title="جزئیات">
          <DetailList
            items={[
              { label: "نوع", value: <strong>{row.typeLabel ?? "غایب"}</strong> },
              { label: "نوع مدت", value: durationKindLabel(row.durationKind) },
              {
                label: durationKindOf(row) === "hourly" ? "تاریخ و ساعت" : "بازه",
                value: durationKindOf(row) === "hourly"
                  ? `${formatJalaliSafe(row.start, true)}${row.startTime && row.endTime ? ` · ${row.startTime} تا ${row.endTime}` : ""}`
                  : `${formatJalaliSafe(row.start, true)} تا ${formatJalaliSafe(row.end, true)}`,
              },
              {
                label: durationKindOf(row) === "hourly" ? "مدت" : "روز کاری",
                value: durationKindOf(row) === "hourly" ? formatRequestDuration(row) : row.workingDays,
              },
              { label: "توضیح", value: row.reason },
              { label: "جانشین", value: row.replacementName },
              { label: "دلیل تصمیم", value: row.decisionComment },
              { label: "اصلاحیه", value: row.supersedesId ? <Link to={`/requests/${row.supersedesId}`}>درخواست {row.supersedesId}</Link> : null },
            ]}
          />
          <div className="toolbar mt">
            {row.canDecide && open && (
              <>
                <Button loading={busy === "approve"} disabled={busy !== null} onClick={() => void act("approve")}>تأیید</Button>
                <Button variant="danger" disabled={busy !== null} onClick={() => setReject(true)}>رد</Button>
              </>
            )}
            {own && open && (
              <Button
                variant="secondary"
                disabled={busy !== null}
                onClick={() => setPending({ action: "cancel", title: "لغو درخواست", message: "درخواست لغو می‌شود. این کار قابل بازگشت نیست.", label: "لغو درخواست", danger: true })}
              >
                لغو
              </Button>
            )}
            {own && row.status === "APPROVED" && (
              <>
                <Button
                  variant="secondary"
                  disabled={busy !== null}
                  onClick={() => setPending({ action: "withdraw", title: "درخواست لغو مرخصی", message: "درخواست لغو مرخصی تأییدشده برای تصمیم‌گیری ارسال می‌شود.", label: "ارسال درخواست لغو" })}
                >
                  درخواست لغو
                </Button>
                <Button
                  variant="secondary"
                  disabled={busy !== null}
                  onClick={() => setPending({ action: "amend", title: "درخواست اصلاح", message: "یک درخواست اصلاحی جدید برای این مرخصی ایجاد می‌شود. فقط یک اصلاح فعال مجاز است.", label: "ایجاد اصلاحیه" })}
                >
                  درخواست اصلاح
                </Button>
              </>
            )}
            {isHr && row.status === "PENDING" && (
              <Button
                variant="secondary"
                disabled={busy !== null}
                onClick={() => setPending({ action: "escalate", title: "ارجاع به HR", message: "درخواست به صف منابع انسانی ارجاع می‌شود.", label: "ارجاع" })}
              >
                ارجاع به HR
              </Button>
            )}
          </div>
        </Card>
        {showExtras && (
          <Card title="نظرها">
            {commentsError && <ErrorState message={commentsError} onRetry={() => void rc()} />}
            {!commentsError && !(comments ?? []).length && <p className="muted">هنوز نظری ثبت نشده است.</p>}
            <ul className="list comments">
              {(comments ?? []).map((c) => (
                <li key={c.id}>
                  <div>
                    <strong>{c.authorName}</strong>
                    <div className="hint">{formatJalaliSafe(c.createdAt)}</div>
                    <div>{c.body}</div>
                  </div>
                </li>
              ))}
            </ul>
            <form onSubmit={addNote} noValidate>
              <Field label="نظر جدید" error={noteError}>
                <Textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="نظر…" />
              </Field>
              <Button type="submit" variant="secondary" size="sm" loading={noteBusy}>ارسال نظر</Button>
            </form>
          </Card>
        )}
      </div>
      {deadline && deadline.kind !== "no_policy" && (
        <Card title="مهلت تأیید و لغو خودکار">
          <DetailList
            items={[
              { label: "وضعیت مهلت", value: <DeadlineLabel info={deadline} /> },
              { label: "زمان انقضا", value: deadline.expiresAt ? formatJalaliSafe(deadline.expiresAt, true) : (row.expiresAt ? formatJalaliSafe(row.expiresAt, true) : null) },
              { label: "آخرین روز تصمیم‌گیری", value: deadline.lastValid ? formatJalaliSafe(deadline.lastValid, true) : null },
              { label: "زمان لغو خودکار", value: deadline.cancelledAt ? formatJalaliSafe(deadline.cancelledAt, true) : null },
              { label: "علت لغو خودکار", value: deadline.reason },
              { label: "مهلت ثبت‌شده برای این درخواست", value: row.autoCancelPolicyDays ? `${row.autoCancelPolicyDays} روز تقویمی` : null },
            ]}
          />
          <p className="hint">مهلت هنگام ثبت روی درخواست قفل می‌شود؛ تغییر بعدی تنظیم، درخواست‌های قبلی را تغییر نمی‌دهد.</p>
        </Card>
      )}
      {showExtras && (
        <Card title="پیوست‌ها">
          {filesError && <ErrorState message={filesError} onRetry={() => void rf()} />}
          {!filesError && !(files ?? []).length && <p className="muted">پیوستی وجود ندارد.</p>}
          <ul className="list">
            {(files ?? []).map((f) => (
              <li key={f.id}>
                {f.scanStatus === "clean" && f.downloadUrl
                  ? <a href={f.downloadUrl}>{f.fileName}</a>
                  : <span>{f.fileName}{f.scanStatus && f.scanStatus !== "clean"
                    ? ` — ${ATTACHMENT_SCAN_LABEL[f.scanStatus]}` : ""}</span>}
                <span className="hint">{f.sizeLabel}</span>
              </li>
            ))}
          </ul>
          <input
            ref={fileInput} type="file" hidden
            accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
            onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
          />
          <div className="toolbar">
            <Button variant="secondary" size="sm" onClick={() => fileInput.current?.click()}>
              انتخاب فایل
            </Button>
            {selectedFile && <span className="hint">{selectedFile.name}</span>}
            <Button size="sm" loading={fileBusy} disabled={!selectedFile} onClick={() => void addFile()}>
              {DEMO_MODE ? "افزودن پیوست نمایشی" : "بارگذاری پیوست"}
            </Button>
          </div>
        </Card>
      )}
      {pending && (
        <ConfirmDialog
          title={pending.title}
          message={pending.message}
          confirmLabel={pending.label}
          danger={pending.danger}
          busy={busy === pending.action}
          onConfirm={() => void confirmPending()}
          onCancel={() => setPending(null)}
        />
      )}
      {rejectOpen && (
        <Modal title="رد درخواست" onClose={() => { if (busy === null) { setReject(false); setReasonError(""); } }}>
          <Field label="دلیل (الزامی)" required error={reasonError}>
            <Textarea value={reason} onChange={(e) => { setReason(e.target.value); setReasonError(""); }} data-autofocus />
          </Field>
          <div className="toolbar modal-actions">
            <Button variant="danger" loading={busy === "reject"} onClick={() => void submitReject()}>ثبت رد</Button>
            <Button variant="secondary" disabled={busy !== null} onClick={() => { setReject(false); setReasonError(""); }}>انصراف</Button>
          </div>
        </Modal>
      )}
    </>
  );
}

export function HrAbsencePage() {
  const toast = useToast();
  const formRef = useRef<HTMLFormElement>(null);
  const { data: types, error: typesError } = useAsync(() => api.listTypes(), []);
  const { data: employees, error: empError } = useAsync(() => api.employees(), []);
  const [employeeId, setEmp] = useState<number | "">("");
  const [leaveTypeKey, setType] = useState("sick");
  const [start, setStart] = useState(todayIso());
  const [end, setEnd] = useState(todayIso());
  const [workingDays, setDays] = useState("1.0");
  const [estimated, setEstimated] = useState(true);
  const [estimating, setEstimating] = useState(false);
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState<number | null>(null);
  const usable = (types ?? []).filter((x) => x.enabled);
  const t = usable.find((x) => x.key === leaveTypeKey) ?? usable[0];

  useEffect(() => {
    if (!estimated || employeeId === "" || !start || !end || start > end) return;
    let alive = true;
    api.estimate(Number(employeeId), start, end)
      .then((e) => { if (alive) setDays(Number(e.days).toFixed(1)); })
      .catch((err) => { if (alive) setFormError(`محاسبه روزهای کاری انجام نشد: ${errorMessage(err)}`); });
    return () => { alive = false; };
  }, [estimated, employeeId, start, end]);

  const estimate = async () => {
    setEstimating(true);
    setFormError("");
    try {
      const e = await api.estimate(employeeId === "" ? null : Number(employeeId), start, end);
      setDays(Number(e.days).toFixed(1));
      setEstimated(true);
    } catch (err) {
      setFormError(errorMessage(err));
    } finally {
      setEstimating(false);
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const found: Record<string, string> = {};
    if (employeeId === "") found.employeeId = "کارمند را انتخاب کنید";
    if (!t) found.leaveTypeKey = "نوع را انتخاب کنید";
    if (!start) found.start = "تاریخ شروع را وارد کنید";
    if (!end) found.end = "تاریخ پایان را وارد کنید";
    if (start && end && start > end) found.end = "تاریخ پایان نباید قبل از تاریخ شروع باشد";
    if (positiveNumber(workingDays) === null) found.workingDays = "روز کاری باید عددی بزرگ‌تر از صفر باشد";
    if (t?.requiresNote && !reason.trim()) found.reason = "توضیح برای این نوع الزامی است";
    setErrors(found);
    setFormError("");
    if (Object.keys(found).length || !t || employeeId === "") {
      focusFirstInvalid(formRef.current);
      return;
    }
    setBusy(true);
    try {
      const row = await api.recordAbsence({
        employeeId: Number(employeeId), leaveTypeKey: t.key, start, end,
        workingDays: normalizeNumberInput(workingDays), reason: reason.trim(),
      });
      toast(`غیبت ${row.id} ثبت شد`, "success");
      setLast(row.id);
      setReason("");
    } catch (err) {
      const ae = asApiError(err);
      setFormError(ae.message);
      setErrors(camelFields(ae.fields));
      focusFirstInvalid(formRef.current);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader title="ثبت غیبت توسط HR" subtitle="مستقیم با وضعیت تأییدشده؛ انواع HR-only از اینجا ثبت می‌شوند" />
      {(typesError || empError) && <ErrorState message={typesError ?? empError ?? ""} />}
      {formError && <Banner tone="danger">{formError}</Banner>}
      {last !== null && (
        <Banner tone="info">
          غیبت ثبت شد. <Link to={`/requests/${last}`}>مشاهده رکورد {last}</Link>
        </Banner>
      )}
      <Card>
        <form onSubmit={submit} ref={formRef} noValidate>
          <div className="grid grid-2">
            <Field label="کارمند" required error={errors.employeeId}>
              <Select value={employeeId} onChange={(e) => setEmp(e.target.value ? Number(e.target.value) : "")}>
                <option value="">انتخاب…</option>
                {(employees ?? []).map((x) => (
                  <option key={x.id} value={x.id}>{x.displayName} ({x.personnelCode}){x.isActive ? "" : " — غیرفعال"}</option>
                ))}
              </Select>
            </Field>
            <Field label="نوع" required error={errors.leaveTypeKey}>
              <Select value={t?.key ?? ""} onChange={(e) => setType(e.target.value)}>
                {usable.map((x) => <option key={x.key} value={x.key}>{x.labelFa}{x.confidential ? " — محرمانه" : ""}</option>)}
              </Select>
            </Field>
            <Field label="از" required error={errors.start}>
              <JalaliDateField value={start} onChange={(v) => { setStart(v); setEstimated(true); }} />
            </Field>
            <Field label="تا" required error={errors.end}>
              <JalaliDateField value={end} min={start || undefined} onChange={(v) => { setEnd(v); setEstimated(true); }} />
            </Field>
          </div>
          <Field label="روز کاری" required error={errors.workingDays} hint="با انتخاب کارمند و بازه، به‌صورت تخمین محاسبه می‌شود و قابل اصلاح است">
            <div className="toolbar">
              <Input
                value={workingDays}
                inputMode="decimal"
                dir="ltr"
                className="ltr-input"
                onChange={(e) => { setDays(e.target.value); setEstimated(false); }}
              />
              <Button type="button" variant="secondary" loading={estimating} disabled={employeeId === ""} onClick={() => void estimate()}>تخمین</Button>
              {estimated && employeeId !== "" && <Badge tone="info">تخمین</Badge>}
            </div>
          </Field>
          <Field label="توضیح" required={!!t?.requiresNote} error={errors.reason}>
            <Textarea value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          <Button type="submit" loading={busy}>ثبت تأییدشده</Button>
        </form>
      </Card>
    </>
  );
}
