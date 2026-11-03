import { ChangeEvent, FormEvent, useMemo, useRef, useState } from "react";
import { DEMO_MODE, api } from "../api";
import { asApiError, errorMessage } from "../api/errors";
import type { Entitlement, ImportPreview, LeaveType } from "../api/types";
import {
  Badge, Banner, Button, Card, Checkbox, DataTable, Empty, ErrorState, Field, Input, LoadingBlock,
  Modal, PageHeader, SearchInput, Segmented, Select, Tabs, Textarea, type Column,
} from "../components/ui";
import { DonutChart } from "../components/charts";
import { camelFields, nonZeroNumber } from "../lib/forms";
import { focusFirstInvalid } from "../lib/hooks";
import { jalaliNow, normalizeNumberInput } from "../lib/jalali";
import { hasAny } from "../lib/roles";
import { useAsync } from "../lib/useAsync";
import { useAuth } from "../state/Auth";
import { useToast } from "../state/Toast";

export function BalancesPage() {
  const { user } = useAuth();
  const year = jalaliNow().jy;
  const canPick = hasAny(user?.roles ?? [], "hr", "manager");
  const [employeeId, setEmployeeId] = useState<number | "">("");
  const { data: employees, error: employeesError, reload: reloadEmployees } = useAsync(
    () => (canPick ? api.employees() : Promise.resolve([])), [canPick],
  );
  const { data, error, loading, reload } = useAsync(
    () => api.balances(year, employeeId === "" ? undefined : Number(employeeId)), [year, employeeId],
  );
  return (
    <>
      <PageHeader title="مانده سهمیه" subtitle={`سال ${year} · فرمول: کل − استفاده‌شده ؛ قابل استفاده پس از کسر در انتظار`} />
      {employeesError && <ErrorState message={employeesError} onRetry={() => void reloadEmployees()} />}
      {canPick && (
        <div className="toolbar">
          <label htmlFor="bal-emp" className="muted">کارمند:</label>
          <select id="bal-emp" value={employeeId} onChange={(e) => setEmployeeId(e.target.value ? Number(e.target.value) : "")}>
            <option value="">خودم</option>
            {(employees ?? []).filter((e) => e.id !== user?.employeeId).map((e) => <option key={e.id} value={e.id}>{e.displayName}</option>)}
          </select>
        </div>
      )}
      {error && <ErrorState message={error} onRetry={() => void reload()} />}
      {loading && !data ? <LoadingBlock /> : null}
      {data && !data.length && <Empty title="سهمیه‌ای ثبت نشده است" />}
      <div className="grid grid-2">
        {data?.map((b) => {
          const total = Number(b.total) || 0;
          const used = Number(b.used) || 0;
          const pending = Number(b.pending) || 0;
          return (
            <Card key={b.leaveTypeKey} title={b.leaveTypeLabel}>
              <p>کل {b.total} (پایه {b.base} + انتقال {b.carryOver} + اصلاح {b.adjustment})</p>
              <div className="meter stacked" aria-hidden="true">
                <div className="meter-fill" style={{ width: `${total ? Math.min(100, (used / total) * 100) : 0}%` }} />
                <div className="meter-fill warn" style={{ width: `${total ? Math.min(100, (pending / total) * 100) : 0}%` }} />
              </div>
              <p>استفاده‌شده {b.used} · در انتظار {b.pending}</p>
              <p><strong>مانده {b.remaining}</strong> · قابل استفاده {b.usable}</p>
            </Card>
          );
        })}
      </div>
    </>
  );
}

export function EntitlementsPage() {
  const toast = useToast();
  const formRef = useRef<HTMLFormElement>(null);
  const year = jalaliNow().jy;
  const { data, reload, error, loading } = useAsync(() => api.entitlements(year), [year]);
  const [days, setDays] = useState("1.0");
  const [reason, setReason] = useState("");
  const [id, setId] = useState<number | "">("");
  const [filter, setFilter] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const rows = useMemo(() => {
    const q = filter.trim();
    return (data ?? []).filter((e) => !q || e.employeeName.includes(q) || e.leaveTypeLabel.includes(q));
  }, [data, filter]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const found: Record<string, string> = {};
    if (id === "") found.id = "ردیف سهمیه را انتخاب کنید";
    if (nonZeroNumber(days) === null) found.days = "عدد غیرصفر (مثبت یا منفی) وارد کنید";
    if (!reason.trim()) found.reason = "دلیل اصلاح الزامی است";
    setErrors(found);
    if (Object.keys(found).length || id === "") { focusFirstInvalid(formRef.current); return; }
    setBusy(true);
    try {
      await api.adjustEntitlement(Number(id), normalizeNumberInput(days), reason.trim());
      toast("اصلاح شد", "success");
      setReason("");
      await reload();
    } catch (err) {
      const ae = asApiError(err);
      toast(ae.message, "danger");
      setErrors(camelFields(ae.fields));
    } finally {
      setBusy(false);
    }
  };

  const columns: Column<Entitlement>[] = [
    { key: "employeeName", label: "کارمند" },
    { key: "leaveTypeLabel", label: "نوع" },
    { key: "base", label: "پایه" },
    { key: "carryOver", label: "انتقال" },
    { key: "adjustment", label: "اصلاح" },
    { key: "total", label: "جمع", render: (r) => (Number(r.base) + Number(r.carryOver) + Number(r.adjustment)).toFixed(1) },
  ];

  return (
    <>
      <PageHeader title="سهمیه‌ها" subtitle="اصلاح دستی فقط با دلیل و ثبت حسابرسی" />
      {error && <ErrorState message={error} onRetry={() => void reload()} />}
      <Card title="اصلاح">
        <form onSubmit={submit} ref={formRef} noValidate>
          <div className="grid grid-3">
            <Field label="ردیف" required error={errors.id}>
              <Select value={id} onChange={(e) => setId(e.target.value ? Number(e.target.value) : "")}>
                <option value="">انتخاب…</option>
                {data?.map((e) => <option key={e.id} value={e.id}>{e.employeeName} · {e.leaveTypeLabel}</option>)}
              </Select>
            </Field>
            <Field label="روز (مثبت یا منفی)" required error={errors.days}>
              <Input value={days} inputMode="decimal" dir="ltr" className="ltr-input" onChange={(e) => setDays(e.target.value)} />
            </Field>
            <Field label="دلیل" required error={errors.reason}>
              <Input value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
          </div>
          <Button type="submit" loading={busy}>ثبت اصلاح</Button>
        </form>
      </Card>
      <div className="mt">
        <div className="toolbar">
          <SearchInput aria-label="جستجوی سهمیه بر اساس کارمند یا نوع" placeholder="جستجوی کارمند یا نوع" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
        {loading && !data ? <LoadingBlock /> : (
          <DataTable columns={columns} rows={rows} rowKey={(r) => r.id} caption={`سهمیه‌های سال ${year}`} />
        )}
      </div>
    </>
  );
}

const SAMPLE_CSV = "personnel_code,type,year,days\n1001,annual,1405,30\n9999,annual,1405,10";

export function ImportPage() {
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [csv, setCsv] = useState(DEMO_MODE ? SAMPLE_CSV : "");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [busy, setBusy] = useState<"dry" | "save" | null>(null);
  const [error, setError] = useState("");

  const run = async (dryRun: boolean) => {
    if (!csv.trim()) { setError("متن CSV خالی است"); return; }
    setBusy(dryRun ? "dry" : "save");
    setError("");
    try {
      const r = await api.importEntitlements(csv, dryRun);
      setPreview(r);
      if (!dryRun) toast(r.rejected ? "ذخیره نشد؛ خطاها را اصلاح کنید" : "ذخیره شد", r.rejected ? "danger" : "success");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const onFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    if (f.size > 1024 * 1024) { setError("حجم فایل نباید بیش از یک مگابایت باشد"); return; }
    setCsv(await f.text());
    setPreview(null);
    setError("");
  };

  const columns: Column<ImportPreview["rows"][number]>[] = [
    { key: "line", label: "خط" },
    { key: "employee", label: "کارمند" },
    { key: "type", label: "نوع" },
    { key: "year", label: "سال" },
    { key: "days", label: "روز" },
    { key: "ok", label: "وضعیت", render: (r) => (r.ok ? <Badge tone="ok">قبول</Badge> : <Badge tone="danger">{r.error ?? "رد"}</Badge>) },
  ];

  return (
    <>
      <PageHeader title="ورود گروهی سهمیه" subtitle="همه‌یا‌هیچ: با وجود یک خطا چیزی ذخیره نمی‌شود. ابتدا Dry Run." />
      {error && <Banner tone="danger">{error}</Banner>}
      <Card>
        <Field label="CSV" hint="ستون‌ها: کد پرسنلی، نوع، سال، روز">
          <Textarea value={csv} dir="ltr" className="ltr-input" spellCheck={false} onChange={(e) => { setCsv(e.target.value); setPreview(null); }} style={{ minHeight: 140 }} />
        </Field>
        <div className="toolbar">
          <input ref={fileRef} type="file" accept=".csv,text/csv" hidden onChange={(e) => void onFile(e)} />
          <Button variant="secondary" onClick={() => fileRef.current?.click()}>بارگذاری فایل CSV</Button>
          <Button variant="secondary" loading={busy === "dry"} disabled={busy !== null} onClick={() => void run(true)}>Dry Run</Button>
          <Button loading={busy === "save"} disabled={busy !== null} onClick={() => void run(false)}>ذخیره</Button>
        </div>
      </Card>
      {preview && (
        <div className="mt">
          <Card title={`${preview.dryRun ? "پیش‌نمایش" : "نتیجه"} · قبول ${preview.accepted} · رد ${preview.rejected}`}>
            <DataTable columns={columns} rows={preview.rows} rowKey={(r) => r.line} caption="نتیجه بررسی CSV" />
          </Card>
        </div>
      )}
    </>
  );
}

const REPORT_KINDS = [
  { id: "absences", label: "غیبت‌ها" },
  { id: "balances", label: "مانده‌ها" },
  { id: "trends", label: "روندها" },
  { id: "process-health", label: "سلامت فرآیند" },
  { id: "bradford", label: "Bradford" },
];

const KIND_SPLIT_LABELS = new Set(["روزانه", "ساعتی"]);
type ChartView = "bars" | "donut" | "pie";

export function ReportsPage() {
  const [view, setView] = useState<ChartView>("donut");
  const [kind, setKind] = useState("absences");
  const { data, error, loading, reload } = useAsync(() => api.reports(kind), [kind]);
  const max = Math.max(1, ...(data?.series.map((s) => s.value) ?? [1]));
  const groups = {
    main: (data?.series ?? []).filter((x) => !KIND_SPLIT_LABELS.has(x.label)),
    kinds: (data?.series ?? []).filter((x) => KIND_SPLIT_LABELS.has(x.label)),
  };
  return (
    <>
      <PageHeader title="گزارش‌ها و خروجی" subtitle="خروجی فایل از API انجام می‌شود؛ اینجا پیش‌نمایش تجمیعی است" />
      <Tabs value={kind} onChange={setKind} items={REPORT_KINDS} label="نوع گزارش" />
      <div className="toolbar">
        {DEMO_MODE ? (
          <span className="hint">خروجی CSV/XLSX در حالت Demo در دسترس نیست.</span>
        ) : (
          <span className="hint">خروجی CSV/XLSX هنوز در API عملیاتی پیاده‌سازی نشده است.</span>
        )}
      </div>
      {error && <ErrorState message={error} onRetry={() => void reload()} />}
      {data?.note && <Banner tone="info">{data.note}</Banner>}
      <Segmented
        label="شکل نمودار"
        value={view}
        onChange={(v) => setView(v as ChartView)}
        items={[{ id: "donut", label: "حلقه‌ای" }, { id: "pie", label: "دایره‌ای" }, { id: "bars", label: "میله‌ای" }]}
      />
      {view !== "bars" && (
        <div className={`grid ${groups.kinds.length ? "grid-2" : ""}`}>
          <Card title={data?.title ?? "گزارش"}>
            <DonutChart
              title={data?.title ?? "گزارش"} variant={view} data={groups.main}
              loading={loading && !data}
              emptyHint="احتمالاً گروه کوچک سرکوب شده یا داده‌ای ثبت نشده است."
            />
          </Card>
          {groups.kinds.length > 0 && (
            <Card title="ترکیب روزانه و ساعتی">
              <DonutChart title="ترکیب روزانه و ساعتی" variant={view} data={groups.kinds} />
            </Card>
          )}
        </div>
      )}
      {view === "bars" && <Card title={data?.title ?? "گزارش"}>
        {loading && !data ? <LoadingBlock /> : (
          <div className="bar-chart">
            {data?.series.map((s) => (
              <div key={s.label} className="bar-row">
                <span>{s.label}</span>
                <div className="bar-track" role="meter" aria-label={s.label} aria-valuemin={0} aria-valuemax={max} aria-valuenow={s.value}>
                  <div className="bar-fill" style={{ width: `${(s.value / max) * 100}%` }} />
                </div>
                <span>{s.value}</span>
              </div>
            ))}
            {data && !data.series.length && <p>داده‌ای برای نمایش نیست (احتمالاً سرکوب گروه کوچک).</p>}
          </div>
        )}
      </Card>}
    </>
  );
}

const KEY_RE = /^[a-z][a-z0-9_]{1,31}$/;
const textLength = (value: string) => Array.from(value).length;

export function LeaveTypesPage() {
  const toast = useToast();
  const formRef = useRef<HTMLFormElement>(null);
  const submitLock = useRef(false);
  const toggleLock = useRef(false);
  const { user } = useAuth();
  const canManage = hasAny(user?.roles ?? [], "admin");
  const { data, reload, error, loading } = useAsync(() => api.listTypes(), []);
  const [labelFa, setLabel] = useState("");
  const [key, setKey] = useState("");
  const [color, setColor] = useState("#0f4c5c");
  const [counts, setCounts] = useState(false);
  const [requiresApproval, setRequiresApproval] = useState(true);
  const [needNote, setNeedNote] = useState(false);
  const [needRep, setNeedRep] = useState(false);
  const [confidential, setConfidential] = useState(false);
  const [employeeRequestable, setEmployeeRequestable] = useState(true);
  const [editing, setEditing] = useState<LeaveType | null>(null);
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [mutationError, setMutationError] = useState("");
  const [busy, setBusy] = useState(false);
  const [toggleTarget, setToggleTarget] = useState<LeaveType | null>(null);
  const [toggleReason, setToggleReason] = useState("");
  const [toggleError, setToggleError] = useState("");
  const [toggleReasonError, setToggleReasonError] = useState("");
  const [toggleBusy, setToggleBusy] = useState(false);

  const resetForm = () => {
    setKey("");
    setLabel("");
    setColor("#0f4c5c");
    setCounts(false);
    setRequiresApproval(true);
    setNeedNote(false);
    setNeedRep(false);
    setConfidential(false);
    setEmployeeRequestable(true);
    setReason("");
    setEditing(null);
    setErrors({});
    setMutationError("");
  };

  const edit = (type: LeaveType) => {
    setEditing(type);
    setKey(type.key);
    setLabel(type.labelFa);
    setColor(type.color);
    setCounts(type.countsAgainstBalance);
    setRequiresApproval(type.requiresApproval);
    setNeedNote(type.requiresNote);
    setNeedRep(type.requiresReplacement);
    setConfidential(type.confidential);
    setEmployeeRequestable(type.employeeRequestable);
    setReason("");
    setErrors({});
    setMutationError("");
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!canManage || submitLock.current || toggleLock.current) return;
    const found: Record<string, string> = {};
    if (!KEY_RE.test(key.trim())) found.key = "کلید لاتین (حروف کوچک، عدد و _؛ شروع با حرف، حداقل ۲ و حداکثر ۳۲ نویسه)";
    else if (data?.some((t) => t.key === key.trim() && t.id !== editing?.id)) found.key = "این کلید قبلاً استفاده شده است";
    if (!labelFa.trim()) found.labelFa = "عنوان فارسی را وارد کنید";
    else if (textLength(labelFa.trim()) > 128) found.labelFa = "عنوان حداکثر ۱۲۸ نویسه است";
    if (!reason.trim()) found.reason = "دلیل تغییر را وارد کنید";
    else if (textLength(reason.trim()) > 500) found.reason = "دلیل تغییر حداکثر ۵۰۰ نویسه است";
    setErrors(found);
    setMutationError("");
    if (Object.keys(found).length) { focusFirstInvalid(formRef.current); return; }
    submitLock.current = true;
    setBusy(true);
    try {
      await api.saveType({
        ...(editing ? { id: editing.id } : {}),
        key: key.trim(), labelFa: labelFa.trim(), color,
        countsAgainstBalance: counts, requiresNote: needNote, requiresReplacement: needRep,
        confidential, employeeRequestable, requiresApproval, reason: reason.trim(),
        ...(editing ? {} : { enabled: true }),
      });
      toast(editing ? "تغییرات ذخیره شد" : "نوع مرخصی ایجاد شد", "success");
      resetForm();
      await reload();
    } catch (err) {
      const ae = asApiError(err);
      toast(ae.message, "danger");
      setErrors(camelFields(ae.fields));
      setMutationError(ae.message);
    } finally {
      submitLock.current = false;
      setBusy(false);
    }
  };

  const toggle = async (e: FormEvent) => {
    e.preventDefault();
    const type = toggleTarget;
    if (!canManage || !type || submitLock.current || toggleLock.current) return;
    if (!toggleReason.trim()) {
      setToggleReasonError("دلیل تغییر را وارد کنید");
      return;
    }
    if (textLength(toggleReason.trim()) > 500) {
      setToggleReasonError("دلیل تغییر حداکثر ۵۰۰ نویسه است");
      return;
    }
    setToggleReasonError("");
    setToggleError("");
    toggleLock.current = true;
    setToggleBusy(true);
    try {
      await api.saveType({
        id: type.id, key: type.key, labelFa: type.labelFa,
        enabled: !type.enabled, reason: toggleReason.trim(),
      });
      setToggleTarget(null);
      setToggleReason("");
      await reload();
      toast(type.enabled ? "نوع مرخصی غیرفعال شد" : "نوع مرخصی فعال شد", "success");
    } catch (err) {
      const ae = asApiError(err);
      toast(ae.message, "danger");
      setToggleError(ae.message);
      setToggleReasonError(ae.fields.reason ?? "");
    } finally {
      toggleLock.current = false;
      setToggleBusy(false);
    }
  };

  const yn = (v: boolean) => (v ? "بله" : "خیر");
  const columns: Column<LeaveType>[] = [
    { key: "key", label: "کلید", ltr: true },
    { key: "labelFa", label: "عنوان", render: (t) => <><span className="chip-dot" style={{ background: t.color }} aria-hidden="true" />{t.labelFa}</> },
    { key: "countsAgainstBalance", label: "سهمیه", render: (t) => yn(t.countsAgainstBalance) },
    { key: "employeeRequestable", label: "کارمند", render: (t) => yn(t.employeeRequestable) },
    { key: "confidential", label: "محرمانه", render: (t) => yn(t.confidential) },
    { key: "enabled", label: "فعال", render: (t) => <Badge tone={t.enabled ? "ok" : "muted"}>{yn(t.enabled)}</Badge> },
  ];
  if (canManage) columns.push({
      key: "actions", label: "عملیات",
      render: (t) => (
        <div className="toolbar">
          <Button variant="secondary" size="sm" disabled={busy || toggleBusy} onClick={() => edit(t)}>ویرایش</Button>
          <Button variant="secondary" size="sm" disabled={busy} onClick={() => {
            setToggleTarget(t);
            setToggleReason("");
            setToggleReasonError("");
            setToggleError("");
          }}>
            {t.enabled ? "غیرفعال‌سازی" : "فعال‌سازی"}
          </Button>
        </div>
      ),
    });

  return (
    <>
      <PageHeader title="انواع مرخصی" />
      {error && <ErrorState message={error} onRetry={() => void reload()} />}
      {canManage && (
        <Card title={editing ? "ویرایش نوع مرخصی" : "افزودن نوع مرخصی"}>
          <form onSubmit={submit} ref={formRef} noValidate>
            {mutationError && <ErrorState message={mutationError} />}
            <div className="grid grid-3">
              <Field label="کلید لاتین" required error={errors.key}>
                <Input value={key} maxLength={32} dir="ltr" className="ltr-input" autoCapitalize="none" spellCheck={false} onChange={(e) => setKey(e.target.value)} />
              </Field>
              <Field label="عنوان فارسی" required error={errors.labelFa}>
                <Input value={labelFa} maxLength={256} onChange={(e) => setLabel(e.target.value)} />
              </Field>
              <Field label="رنگ در تقویم">
                <Input type="color" value={color} onChange={(e) => setColor(e.target.value)} />
              </Field>
            </div>
            <fieldset className="field-group">
              <legend>ویژگی‌ها</legend>
              <div className="check-row">
                <Checkbox label="از سهمیه کسر شود" checked={counts} onChange={(e) => setCounts(e.target.checked)} />
                <Checkbox label="تأیید مدیر لازم است" checked={requiresApproval} onChange={(e) => setRequiresApproval(e.target.checked)} />
                <Checkbox label="توضیح الزامی" checked={needNote} onChange={(e) => setNeedNote(e.target.checked)} />
                <Checkbox label="جانشین الزامی" checked={needRep} onChange={(e) => setNeedRep(e.target.checked)} />
                <Checkbox label="کارمند می‌تواند درخواست کند" checked={employeeRequestable} disabled={confidential} onChange={(e) => setEmployeeRequestable(e.target.checked)} />
                <Checkbox label="محرمانه (فقط HR ثبت می‌کند)" checked={confidential} onChange={(e) => {
                  setConfidential(e.target.checked);
                  if (e.target.checked) setEmployeeRequestable(false);
                }} />
              </div>
            </fieldset>
            <Field label="دلیل تغییر (الزامی)" required error={errors.reason} hint="حداکثر ۵۰۰ نویسه؛ دلیل ثبت‌شده در Audit نگهداری می‌شود.">
              <Input value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} />
            </Field>
            <div className="toolbar">
              <Button type="submit" loading={busy} disabled={toggleBusy}>{editing ? "ذخیره تغییرات" : "ایجاد نوع مرخصی"}</Button>
              {editing && <Button variant="secondary" disabled={busy || toggleBusy} onClick={resetForm}>انصراف از ویرایش</Button>}
            </div>
          </form>
        </Card>
      )}
      <div className="mt">
        {loading && !data ? <LoadingBlock /> : <DataTable columns={columns} rows={data ?? []} rowKey={(t) => t.id} caption="انواع مرخصی" />}
      </div>
      {toggleTarget && (
        <Modal
          title={toggleTarget.enabled ? "غیرفعال‌سازی نوع مرخصی" : "فعال‌سازی نوع مرخصی"}
          onClose={() => { if (!toggleBusy) setToggleTarget(null); }}
        >
          <form className="modal-body" onSubmit={(e) => void toggle(e)} noValidate>
            <p>{toggleTarget.labelFa} ({toggleTarget.key})</p>
            {toggleError && <ErrorState message={toggleError} />}
            <Field label="دلیل تغییر (الزامی)" required error={toggleReasonError} hint="حداکثر ۵۰۰ نویسه؛ دلیل ثبت‌شده در Audit نگهداری می‌شود.">
              <Input value={toggleReason} maxLength={1000} onChange={(e) => {
                setToggleReason(e.target.value);
                setToggleReasonError("");
              }} />
            </Field>
            <div className="toolbar modal-actions">
              <Button type="submit" loading={toggleBusy}>تأیید</Button>
              <Button variant="secondary" disabled={toggleBusy} onClick={() => setToggleTarget(null)}>انصراف</Button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
