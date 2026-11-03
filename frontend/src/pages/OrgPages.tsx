import { FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, DEMO_MODE } from "../api";
import { asApiError, errorMessage } from "../api/errors";
import type {
  Company, ContractPeriod, Department, Employee, EmployeeAccountStatus,
  EmployeeOnboardingResult, Holiday,
} from "../api/types";
import { JalaliDateField } from "../components/JalaliDateField";
import {
  Badge, Button, Card, Checkbox, ConfirmDialog, DataTable, ErrorState, Field, Input, LoadingBlock,
  Modal, PageHeader, SearchInput, Select, Textarea, type Column,
} from "../components/ui";
import { camelFields } from "../lib/forms";
import { focusFirstInvalid, useDebounced } from "../lib/hooks";
import { MONTH_NAMES, PY_WEEKDAY_LABELS, formatJalaliSafe, monthLength, todayIso, MAX_YEAR, MIN_YEAR } from "../lib/jalali";
import { hasAny } from "../lib/roles";
import { useAsync } from "../lib/useAsync";
import { useAuth } from "../state/Auth";
import { useToast } from "../state/Toast";

export function EmployeesPage() {
  const toast = useToast();
  const formRef = useRef<HTMLFormElement>(null);
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 300);
  const { data, reload, error, loading } = useAsync(() => api.employees(dq), [dq]);
  const { data: allEmployees, error: allEmployeesError } = useAsync(() => api.employees(), []);
  const { data: companies } = useAsync(() => api.companies(), []);
  const { data: departments, error: departmentsError } = useAsync(() => api.departments(), []);
  const { data: sites, error: sitesError } = useAsync(() => api.sites(), []);
  const { user } = useAuth();
  const canEdit = hasAny(user?.roles ?? [], "hr", "admin");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [code, setCode] = useState("");
  const [email, setEmail] = useState("");
  const [companyId, setCid] = useState<number | "">("");
  const [departmentId, setDepartmentId] = useState<number | "">("");
  const [siteId, setSiteId] = useState<number | "">("");
  const [managerId, setManagerId] = useState<number | "">("");
  const [startDate, setStartDate] = useState(todayIso());
  const [endDate, setEndDate] = useState("");
  const [hasContract, setHasContract] = useState(false);
  const [contractStart, setContractStart] = useState(todayIso());
  const [contractEnd, setContractEnd] = useState("");
  const [contractPercent, setContractPercent] = useState("100");
  const [workDays, setWorkDays] = useState<number[]>([5, 6, 0, 1, 2]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [createdEmployee, setCreatedEmployee] = useState<EmployeeOnboardingResult | null>(null);
  const [contractWarning, setContractWarning] = useState("");
  const [inviteTarget, setInviteTarget] = useState<Employee | null>(null);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteBusy, setInviteBusy] = useState(false);
  const [inviteError, setInviteError] = useState("");
  const [lastInvitation, setLastInvitation] = useState<{
    employeeName: string;
    status: "sent" | "pending";
    message: string;
    developmentUrl?: string;
  } | null>(null);
  const [actionTarget, setActionTarget] = useState<{ employee: Employee; action: "revoke" | "suspend" } | null>(null);
  const [actionReason, setActionReason] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState("");

  useEffect(() => {
    if (companyId === "" && companies?.length === 1) setCid(companies[0].id);
  }, [companies, companyId]);

  const availableDepartments = departments?.filter((item) => item.companyId === companyId) ?? [];
  const availableSites = sites?.filter((item) => item.companyId === companyId && item.isActive) ?? [];
  const availableManagers = allEmployees?.filter((item) =>
    item.companyId === companyId && item.id !== user?.employeeId && item.isActive,
  ) ?? [];

  const resetForm = () => {
    setFirstName("");
    setLastName("");
    setCode("");
    setEmail("");
    setDepartmentId("");
    setSiteId("");
    setManagerId("");
    setStartDate(todayIso());
    setEndDate("");
    setHasContract(false);
    setContractStart(todayIso());
    setContractEnd("");
    setContractPercent("100");
    setWorkDays([5, 6, 0, 1, 2]);
    setErrors({});
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const found: Record<string, string> = {};
    if (!firstName.trim()) found.firstName = "نام را وارد کنید";
    if (!lastName.trim()) found.lastName = "نام خانوادگی را وارد کنید";
    if (!code.trim()) found.personnelCode = "کد پرسنلی را وارد کنید";
    if (!email.trim() || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) found.email = "ایمیل معتبر را وارد کنید";
    if (companyId === "") found.companyId = "شرکت را انتخاب کنید";
    if (!startDate) found.startDate = "تاریخ شروع اشتغال را وارد کنید";
    if (endDate && startDate && endDate < startDate) found.endDate = "پایان اشتغال نباید پیش از شروع باشد";
    if (hasContract) {
      const percent = Number(contractPercent);
      if (!contractStart) found.contractStart = "تاریخ شروع قرارداد را وارد کنید";
      if (contractEnd && contractStart && contractEnd < contractStart) found.contractEnd = "پایان قرارداد نباید پیش از شروع باشد";
      if (!workDays.length) found.workDays = "دست‌کم یک روز کاری انتخاب کنید";
      if (!Number.isFinite(percent) || percent <= 0 || percent > 100) found.contractPercent = "درصد باید بین ۱ تا ۱۰۰ باشد";
    }
    setErrors(found);
    if (Object.keys(found).length || companyId === "") { focusFirstInvalid(formRef.current); return; }
    setBusy(true);
    setCreatedEmployee(null);
    setContractWarning("");
    try {
      const created = await api.saveEmployee({
        firstName: firstName.trim(),
        lastName: lastName.trim(),
        personnelCode: code.trim(),
        email: email.trim(),
        companyId: Number(companyId),
        departmentId: departmentId === "" ? null : Number(departmentId),
        siteId: siteId === "" ? null : Number(siteId),
        managerId: managerId === "" ? null : Number(managerId),
        startDate,
        endDate: endDate || null,
      });
      setCreatedEmployee(created);
      if (hasContract) {
        try {
          await api.saveContract({
            employeeId: created.id,
            start: contractStart,
            end: contractEnd || null,
            workDays,
            contractPercent: Number(contractPercent),
          });
        } catch (contractError) {
          setContractWarning(errorMessage(contractError));
        }
      }
      toast(
        created.invitationDelivery === "sent"
          ? "پرونده ساخته شد و دعوت ایمیلی ارسال شد"
          : "پرونده ساخته شد؛ ارسال دعوت نیازمند پیگیری است",
        created.invitationDelivery === "sent" ? "success" : "info",
      );
      resetForm();
      await reload();
    } catch (err) {
      const ae = asApiError(err);
      toast(ae.message, "danger");
      setErrors(camelFields(ae.fields));
    } finally {
      setBusy(false);
    }
  };

  const sendInvitation = async (event: FormEvent) => {
    event.preventDefault();
    if (!inviteTarget || inviteBusy || !inviteEmail.trim()) return;
    setInviteBusy(true);
    setInviteError("");
    try {
      const result = await api.inviteEmployee(inviteTarget.id, inviteEmail.trim());
      setLastInvitation({
        employeeName: inviteTarget.displayName,
        status: result.status,
        message: result.message_fa,
        developmentUrl: result.developmentActivationUrl,
      });
      toast(
        result.status === "sent" ? "دعوت ارسال شد" : "دعوت در وضعیت انتظار است",
        result.status === "sent" ? "success" : "info",
      );
      setInviteTarget(null);
      await reload();
    } catch (err) {
      setInviteError(errorMessage(err));
    } finally {
      setInviteBusy(false);
    }
  };

  const performAccountAction = async () => {
    if (!actionTarget || actionBusy || !actionReason.trim()) return;
    setActionBusy(true);
    setActionError("");
    try {
      if (actionTarget.action === "revoke") {
        await api.revokeEmployeeInvitation(actionTarget.employee.id, actionReason.trim());
      } else {
        await api.suspendEmployee(actionTarget.employee.id, actionReason.trim());
      }
      toast(actionTarget.action === "revoke" ? "دعوت لغو شد" : "حساب تعلیق شد", "success");
      setActionTarget(null);
      setActionReason("");
      await reload();
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setActionBusy(false);
    }
  };

  const accountStatusLabel: Record<EmployeeAccountStatus, string> = {
    created: "بدون دعوت",
    invitation_pending: "در انتظار ارسال",
    invitation_sent: "دعوت ارسال‌شده",
    invitation_expired: "دعوت منقضی",
    invitation_revoked: "دعوت لغوشده",
    account_activated: "حساب فعال",
    account_suspended: "حساب تعلیق‌شده",
    account_offboarded: "خاتمه همکاری",
  };
  const accountStatusTone: Record<EmployeeAccountStatus, "ok" | "warn" | "danger" | "info" | "muted"> = {
    created: "warn",
    invitation_pending: "warn",
    invitation_sent: "info",
    invitation_expired: "danger",
    invitation_revoked: "muted",
    account_activated: "ok",
    account_suspended: "danger",
    account_offboarded: "muted",
  };

  const columns: Column<Employee>[] = [
    { key: "displayName", label: "نام" },
    { key: "personnelCode", label: "کد", ltr: true },
    { key: "email", label: "ایمیل", ltr: true, render: (e) => e.email || "—" },
    { key: "companyName", label: "شرکت" },
    { key: "departmentName", label: "واحد" },
    { key: "managerName", label: "مدیر", render: (e) => e.managerName ?? "—" },
    { key: "startDate", label: "شروع اشتغال", render: (e) => formatJalaliSafe(e.startDate) },
    { key: "endDate", label: "پایان اشتغال", render: (e) => e.endDate ? formatJalaliSafe(e.endDate) : "—" },
    { key: "accountStatus", label: "وضعیت حساب", render: (e) => {
      const status = e.accountStatus ?? "created";
      return <Badge tone={accountStatusTone[status]}>{accountStatusLabel[status]}</Badge>;
    } },
    { key: "actions", label: "عملیات", render: (e) => {
      if (!canEdit || DEMO_MODE) return "—";
      const status = e.accountStatus ?? "created";
      const canInvite = !["account_activated", "account_suspended", "account_offboarded"].includes(status);
      return (
        <div className="toolbar">
          {canInvite && (
            <Button size="sm" variant="secondary" onClick={() => {
              setInviteError("");
              setInviteEmail(e.email ?? "");
              setInviteTarget(e);
            }}>
              {status === "invitation_sent" || status === "invitation_pending" ? "ارسال مجدد دعوت" : "ارسال دعوت"}
            </Button>
          )}
          {(status === "invitation_sent" || status === "invitation_pending") && (
            <Button size="sm" variant="danger" onClick={() => {
              setActionError("");
              setActionTarget({ employee: e, action: "revoke" });
              setActionReason("");
            }}>لغو دعوت</Button>
          )}
          {status === "account_activated" && (
            <Button size="sm" variant="danger" onClick={() => {
              setActionError("");
              setActionTarget({ employee: e, action: "suspend" });
              setActionReason("");
            }}>تعلیق حساب</Button>
          )}
        </div>
      );
    } },
  ];

  return (
    <>
      <PageHeader title="کارکنان" subtitle="ایجاد پرونده، دعوت و وضعیت فعال‌سازی حساب" />
      {(error || allEmployeesError || departmentsError || sitesError) && (
        <ErrorState
          message={error ?? allEmployeesError ?? departmentsError ?? sitesError ?? ""}
          onRetry={() => void reload()}
        />
      )}
      <div className="toolbar" role="search">
        <SearchInput aria-label="جستجوی کارمند بر اساس نام یا کد پرسنلی" placeholder="نام یا کد پرسنلی" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {canEdit && DEMO_MODE && (
        <Card title="Onboarding در Demo">
          <p>Demo حساب واقعی نمی‌سازد و ایمیل دعوت یا فعال‌سازی ارسال نمی‌کند. برای onboarding به Backend متصل شوید.</p>
        </Card>
      )}
      {canEdit && !DEMO_MODE && (
        <Card title="ایجاد کارمند و ارسال دعوت">
          <form onSubmit={submit} ref={formRef} noValidate>
            <div className="grid grid-3">
              <Field label="نام" required error={errors.firstName}><Input value={firstName} onChange={(e) => setFirstName(e.target.value)} /></Field>
              <Field label="نام خانوادگی" required error={errors.lastName}><Input value={lastName} onChange={(e) => setLastName(e.target.value)} /></Field>
              <Field label="کد پرسنلی" required error={errors.personnelCode}>
                <Input value={code} dir="ltr" className="ltr-input" onChange={(e) => setCode(e.target.value)} />
              </Field>
              <Field label="ایمیل دعوت" required error={errors.email} hint="دعوت برای ساخت گذرواژه به این نشانی ارسال می‌شود.">
                <Input type="email" value={email} dir="ltr" className="ltr-input" autoComplete="email" onChange={(e) => setEmail(e.target.value)} />
              </Field>
              <Field label="شرکت" required error={errors.companyId}>
                <Select value={companyId} onChange={(e) => {
                  setCid(e.target.value ? Number(e.target.value) : "");
                  setDepartmentId(""); setSiteId(""); setManagerId("");
                }}>
                  <option value="">انتخاب کنید</option>
                  {companies?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </Select>
              </Field>
              <Field label="واحد" error={errors.departmentId}>
                <Select value={departmentId} onChange={(e) => setDepartmentId(e.target.value ? Number(e.target.value) : "")}>
                  <option value="">بدون واحد</option>
                  {availableDepartments.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </Select>
              </Field>
              <Field label="سایت">
                <Select value={siteId} onChange={(e) => setSiteId(e.target.value ? Number(e.target.value) : "")}>
                  <option value="">بدون سایت</option>
                  {availableSites.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </Select>
              </Field>
              <Field label="مدیر مستقیم">
                <Select value={managerId} onChange={(e) => setManagerId(e.target.value ? Number(e.target.value) : "")}>
                  <option value="">بدون مدیر</option>
                  {availableManagers.map((item) => <option key={item.id} value={item.id}>{item.displayName}</option>)}
                </Select>
              </Field>
              <Field label="شروع اشتغال" required error={errors.startDate}>
                <JalaliDateField value={startDate} onChange={(value) => {
                  setStartDate(value);
                  if (contractStart === todayIso()) setContractStart(value);
                }} />
              </Field>
              <Field label="پایان اشتغال (اختیاری)" error={errors.endDate}>
                <JalaliDateField value={endDate} min={startDate || undefined} onChange={setEndDate} />
              </Field>
            </div>
            <Checkbox
              label="ثبت قرارداد و الگوی کاری اولیه"
              checked={hasContract}
              onChange={(event) => setHasContract(event.target.checked)}
            />
            {hasContract && (
              <>
                <div className="grid grid-3 mt">
                  <Field label="شروع قرارداد" required error={errors.contractStart}>
                    <JalaliDateField value={contractStart} onChange={setContractStart} />
                  </Field>
                  <Field label="پایان قرارداد (اختیاری)" error={errors.contractEnd}>
                    <JalaliDateField value={contractEnd} min={contractStart || undefined} onChange={setContractEnd} />
                  </Field>
                  <Field label="درصد قرارداد" required error={errors.contractPercent}>
                    <Input type="number" min={1} max={100} value={contractPercent} dir="ltr" className="ltr-input" onChange={(event) => setContractPercent(event.target.value)} />
                  </Field>
                </div>
                <fieldset className="field-group" aria-describedby={errors.workDays ? "employee-work-days-error" : undefined}>
                  <legend>روزهای کاری</legend>
                  <div className="check-row">
                    {[5, 6, 0, 1, 2, 3, 4].map((day) => (
                      <Checkbox
                        key={day}
                        label={PY_WEEKDAY_LABELS[day]}
                        checked={workDays.includes(day)}
                        onChange={(event) => setWorkDays((current) =>
                          event.target.checked
                            ? [...current, day]
                            : current.filter((value) => value !== day),
                        )}
                      />
                    ))}
                  </div>
                  {errors.workDays && <div className="error" id="employee-work-days-error" role="alert">{errors.workDays}</div>}
                </fieldset>
              </>
            )}
            {contractWarning && (
              <div className="banner danger" role="alert">
                پرونده و دعوت ایجاد شدند، اما ثبت قرارداد ناموفق بود: {contractWarning}{" "}
                <Link to="/hr/contracts">ثبت قرارداد از صفحه قراردادها</Link>
              </div>
            )}
            {createdEmployee && (
              <div className={`banner ${createdEmployee.invitationDelivery === "sent" ? "info" : ""}`} role="status">
                {createdEmployee.invitationMessageFa ?? "پرونده ایجاد شد."}
                {createdEmployee.developmentActivationUrl && (
                  <p>
                    پیوند توسعه (فقط این محیط):{" "}
                    <a href={createdEmployee.developmentActivationUrl} rel="noreferrer">فعال‌سازی</a>
                  </p>
                )}
              </div>
            )}
            <Button type="submit" loading={busy}>ایجاد پرونده و دعوت</Button>
          </form>
        </Card>
      )}
      {lastInvitation && (
        <div className={`banner ${lastInvitation.status === "sent" ? "info" : ""}`} role="status">
          دعوت {lastInvitation.employeeName}: {lastInvitation.message}
          {lastInvitation.developmentUrl && (
            <p><a href={lastInvitation.developmentUrl} rel="noreferrer">پیوند فعال‌سازی توسعه</a></p>
          )}
        </div>
      )}
      <div className="mt">
        {loading && !data ? <LoadingBlock /> : <DataTable columns={columns} rows={data ?? []} rowKey={(e) => e.id} caption="فهرست کارکنان" />}
      </div>
      {inviteTarget && (
        <Modal title={`دعوت ${inviteTarget.displayName}`} onClose={() => setInviteTarget(null)}>
          <form className="modal-body" onSubmit={(event) => void sendInvitation(event)} noValidate>
            {inviteError && <div className="banner danger" role="alert">{inviteError}</div>}
            <Field label="ایمیل دعوت" required>
              <Input type="email" value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} autoComplete="email" dir="ltr" className="ltr-input" required />
            </Field>
            <div className="toolbar modal-actions">
              <Button type="submit" loading={inviteBusy} disabled={!inviteEmail.trim()}>ارسال دعوت</Button>
              <Button type="button" variant="secondary" disabled={inviteBusy} onClick={() => setInviteTarget(null)}>انصراف</Button>
            </div>
          </form>
        </Modal>
      )}
      {actionTarget && (
        <Modal
          title={actionTarget.action === "revoke" ? "لغو دعوت" : "تعلیق حساب"}
          onClose={() => { if (!actionBusy) setActionTarget(null); }}
        >
          <div className="modal-body">
            <p>
              {actionTarget.action === "revoke"
                ? `دعوت ${actionTarget.employee.displayName} باطل خواهد شد.`
                : `ورود ${actionTarget.employee.displayName} مسدود و نشست‌های جاری او باطل خواهند شد.`}
            </p>
            {actionError && <div className="banner danger" role="alert">{actionError}</div>}
            <Field label="دلیل" required>
              <Textarea value={actionReason} maxLength={500} onChange={(event) => setActionReason(event.target.value)} />
            </Field>
            <div className="toolbar modal-actions">
              <Button variant="danger" loading={actionBusy} disabled={!actionReason.trim()} onClick={() => void performAccountAction()}>
                {actionTarget.action === "revoke" ? "لغو دعوت" : "تعلیق حساب"}
              </Button>
              <Button variant="secondary" disabled={actionBusy} onClick={() => setActionTarget(null)}>انصراف</Button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}

export function OrganizationPage() {
  const { user } = useAuth();
  const canEdit = hasAny(user?.roles ?? [], "admin");
  const toast = useToast();
  const { data: companies, reload: rc, error: ce, loading: cl } = useAsync(() => api.companies(), []);
  const { data: deps, reload: rd, error: de, loading: dl } = useAsync(() => api.departments(), []);
  const [cname, setCn] = useState("");
  const [ccode, setCc] = useState("");
  const [dname, setDn] = useState("");
  const [dcode, setDc] = useState("");
  const [companyId, setCid] = useState<number | "">("");
  const [cErr, setCErr] = useState<Record<string, string>>({});
  const [dErr, setDErr] = useState<Record<string, string>>({});
  const [cBusy, setCBusy] = useState(false);
  const [dBusy, setDBusy] = useState(false);
  const cForm = useRef<HTMLFormElement>(null);
  const dForm = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (companyId === "" && companies?.length) setCid(companies[0].id);
  }, [companies, companyId]);

  const addCompany = async (e: FormEvent) => {
    e.preventDefault();
    if (cBusy) return;
    const found: Record<string, string> = {};
    if (!ccode.trim()) found.code = "کد شرکت را وارد کنید";
    if (!cname.trim()) found.name = "نام شرکت را وارد کنید";
    setCErr(found);
    if (Object.keys(found).length) { focusFirstInvalid(cForm.current); return; }
    setCBusy(true);
    try {
      await api.saveCompany({ code: ccode.trim(), name: cname.trim() });
      toast("شرکت افزوده شد", "success");
      setCc(""); setCn("");
      await rc();
    } catch (err) {
      const ae = asApiError(err);
      toast(ae.message, "danger");
      setCErr(camelFields(ae.fields));
    } finally {
      setCBusy(false);
    }
  };

  const addDepartment = async (e: FormEvent) => {
    e.preventDefault();
    if (dBusy) return;
    const found: Record<string, string> = {};
    if (companyId === "") found.companyId = "شرکت را انتخاب کنید";
    if (!dcode.trim()) found.code = "کد واحد را وارد کنید";
    if (!dname.trim()) found.name = "نام واحد را وارد کنید";
    setDErr(found);
    if (Object.keys(found).length || companyId === "") { focusFirstInvalid(dForm.current); return; }
    setDBusy(true);
    try {
      await api.saveDepartment({ code: dcode.trim(), name: dname.trim(), companyId: Number(companyId) });
      toast("واحد افزوده شد", "success");
      setDc(""); setDn("");
      await rd();
    } catch (err) {
      const ae = asApiError(err);
      toast(ae.message, "danger");
      setDErr(camelFields(ae.fields));
    } finally {
      setDBusy(false);
    }
  };

  const companyName = (id: number) => companies?.find((c) => c.id === id)?.name ?? "—";
  const companyCols: Column<Company>[] = [
    { key: "code", label: "کد", ltr: true },
    { key: "name", label: "نام" },
    { key: "isActive", label: "وضعیت", render: (c) => <Badge tone={c.isActive ? "ok" : "muted"}>{c.isActive ? "فعال" : "غیرفعال"}</Badge> },
  ];
  const depCols: Column<Department>[] = [
    { key: "code", label: "کد", ltr: true },
    { key: "name", label: "نام" },
    { key: "companyId", label: "شرکت", render: (d) => companyName(d.companyId) },
  ];

  return (
    <>
      <PageHeader title="ساختار سازمانی" subtitle="حذف شرکت/واحد دارای وابسته در Backend ممنوع است" />
      {(ce || de) && <ErrorState message={ce ?? de ?? ""} onRetry={() => { void rc(); void rd(); }} />}
      <div className="grid grid-2">
        <Card title="شرکت‌ها">
          {canEdit && (
            <form onSubmit={addCompany} ref={cForm} noValidate>
              <Field label="کد" required error={cErr.code}><Input value={ccode} dir="ltr" className="ltr-input" onChange={(e) => setCc(e.target.value)} /></Field>
              <Field label="نام" required error={cErr.name}><Input value={cname} onChange={(e) => setCn(e.target.value)} /></Field>
              <Button type="submit" loading={cBusy}>افزودن شرکت</Button>
            </form>
          )}
          <div className={canEdit ? "mt" : undefined}>
            {cl && !companies ? <LoadingBlock /> : <DataTable columns={companyCols} rows={companies ?? []} rowKey={(c) => c.id} caption="شرکت‌ها" />}
          </div>
        </Card>
        <Card title="واحدها">
          {canEdit && (
            <form onSubmit={addDepartment} ref={dForm} noValidate>
              <Field label="شرکت" required error={dErr.companyId}>
                <Select value={companyId} onChange={(e) => setCid(e.target.value ? Number(e.target.value) : "")}>
                  {companies?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </Select>
              </Field>
              <Field label="کد" required error={dErr.code}><Input value={dcode} dir="ltr" className="ltr-input" onChange={(e) => setDc(e.target.value)} /></Field>
              <Field label="نام" required error={dErr.name}><Input value={dname} onChange={(e) => setDn(e.target.value)} /></Field>
              <Button type="submit" loading={dBusy}>افزودن واحد</Button>
            </form>
          )}
          <div className={canEdit ? "mt" : undefined}>
            {dl && !deps ? <LoadingBlock /> : <DataTable columns={depCols} rows={deps ?? []} rowKey={(d) => d.id} caption="واحدها" />}
          </div>
        </Card>
      </div>
    </>
  );
}

export function HolidaysPage() {
  const { user } = useAuth();
  const canManage = DEMO_MODE
    ? hasAny(user?.roles ?? [], "hr", "admin")
    : hasAny(user?.roles ?? [], "admin");
  const toast = useToast();
  const formRef = useRef<HTMLFormElement>(null);
  const { data, reload, error, loading } = useAsync(() => api.holidays(), []);
  const [label, setLabel] = useState("");
  const [m, setM] = useState(1);
  const [d, setD] = useState("1");
  const [annual, setAnnual] = useState(true);
  const [year, setYear] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [toDelete, setToDelete] = useState<Holiday | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteReason, setDeleteReason] = useState("");
  const [deleteError, setDeleteError] = useState("");

  const sorted = [...(data ?? [])].sort((a, b) => a.jalaliMonth - b.jalaliMonth || a.jalaliDay - b.jalaliDay || (a.jalaliYear ?? 0) - (b.jalaliYear ?? 0));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const found: Record<string, string> = {};
    const day = Number(d);
    const yr = Number(year);
    if (!label.trim()) found.label = "عنوان را وارد کنید";
    if (!Number.isInteger(day) || day < 1) found.jalaliDay = "روز نامعتبر است";
    if (!annual) {
      if (!Number.isInteger(yr) || yr < MIN_YEAR || yr > MAX_YEAR) found.jalaliYear = `سال باید بین ${MIN_YEAR} و ${MAX_YEAR} باشد`;
    }
    if (!found.jalaliDay) {
      // برای تعطیلی سالانه، بلندترین طول ممکن ماه (سال کبیسه) پذیرفته می‌شود
      const limit = annual ? (m <= 6 ? 31 : 30) : (!found.jalaliYear ? monthLength(yr, m) : 31);
      if (day > limit) found.jalaliDay = `${MONTH_NAMES[m - 1]} حداکثر ${limit} روز دارد`;
    }
    setErrors(found);
    if (Object.keys(found).length) { focusFirstInvalid(formRef.current); return; }
    setBusy(true);
    try {
      await api.saveHoliday({ scope: "*", jalaliYear: annual ? null : yr, jalaliMonth: m, jalaliDay: day, label: label.trim() });
      toast("ثبت شد", "success");
      setLabel("");
      await reload();
    } catch (err) {
      const ae = asApiError(err);
      toast(ae.message, "danger");
      setErrors(camelFields(ae.fields));
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async () => {
    if (!toDelete) return;
    if (!deleteReason.trim()) {
      setDeleteError("دلیل غیرفعال‌سازی الزامی است");
      return;
    }
    setDeleting(true);
    try {
      await api.deleteHoliday(toDelete.id, deleteReason.trim());
      toast("غیرفعال شد", "success");
      setToDelete(null);
      setDeleteReason("");
      setDeleteError("");
      await reload();
    } catch (err) {
      toast(errorMessage(err), "danger");
    } finally {
      setDeleting(false);
    }
  };

  const columns: Column<Holiday>[] = [
    { key: "label", label: "عنوان" },
    { key: "date", label: "تاریخ شمسی", render: (h) => `${h.jalaliDay} ${MONTH_NAMES[h.jalaliMonth - 1] ?? h.jalaliMonth}${h.jalaliYear ? ` ${h.jalaliYear}` : " (هر سال)"}` },
    { key: "scope", label: "محدوده", render: (h) => (h.scope === "*" ? "ملی" : h.scope) },
    {
      key: "actions", label: "عملیات",
      render: (h) => (canManage
        ? <Button variant="danger" size="sm" onClick={() => { setToDelete(h); setDeleteReason(""); setDeleteError(""); }}>غیرفعال‌سازی</Button>
        : "—"),
    },
  ];

  return (
    <>
      <PageHeader title="تعطیلات رسمی" subtitle="منبع: ثبت دستی شمسی. مبنای تغییر مقدار تأییدشده مرخصی نیستند." />
      {error && <ErrorState message={error} onRetry={() => void reload()} />}
      {canManage && (
        <Card title="ثبت تعطیلی">
          <form onSubmit={submit} ref={formRef} noValidate>
            <div className="grid grid-3">
              <Field label="عنوان" required error={errors.label}><Input value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
              <Field label="ماه" required>
                <Select value={m} onChange={(e) => setM(Number(e.target.value))}>
                  {MONTH_NAMES.map((n, i) => <option key={n} value={i + 1}>{n}</option>)}
                </Select>
              </Field>
              <Field label="روز" required error={errors.jalaliDay}>
                <Input type="number" min={1} max={31} value={d} dir="ltr" className="ltr-input" onChange={(e) => setD(e.target.value)} />
              </Field>
            </div>
            <Checkbox label="سالانه (بدون سال مشخص)" checked={annual} onChange={(e) => setAnnual(e.target.checked)} />
            {!annual && (
              <Field label="سال شمسی" required error={errors.jalaliYear}>
                <Input type="number" min={MIN_YEAR} max={MAX_YEAR} value={year} dir="ltr" className="ltr-input" placeholder={String(new Date().getFullYear() - 621)} onChange={(e) => setYear(e.target.value)} />
              </Field>
            )}
            <Button type="submit" loading={busy}>ذخیره</Button>
          </form>
        </Card>
      )}
      <div className="mt">
        {loading && !data ? <LoadingBlock /> : <DataTable columns={columns} rows={sorted} rowKey={(h) => h.id} caption="تعطیلات رسمی" />}
      </div>
      {toDelete && (
        <ConfirmDialog
          title="غیرفعال‌سازی تعطیلی"
          message={(
            <>
              <p>تعطیلی «{toDelete.label}» غیرفعال می‌شود؛ سابقه آن حفظ خواهد شد.</p>
              <Field label="دلیل" required error={deleteError}>
                <Input value={deleteReason} onChange={(event) => { setDeleteReason(event.target.value); setDeleteError(""); }} />
              </Field>
            </>
          )}
          confirmLabel="غیرفعال‌سازی"
          danger
          busy={deleting}
          onConfirm={() => void confirmDelete()}
          onCancel={() => setToDelete(null)}
        />
      )}
    </>
  );
}

const DEFAULT_WORK_DAYS = [5, 6, 0, 1, 2]; // شنبه تا چهارشنبه (شاخص پایتون)
const WEEK_ORDER = [5, 6, 0, 1, 2, 3, 4]; // ترتیب نمایش ایرانی

export function ContractsPage() {
  const toast = useToast();
  const formRef = useRef<HTMLFormElement>(null);
  const { data: employees, error: empError } = useAsync(() => api.employees(), []);
  const [employeeId, setEmp] = useState<number | "">("");
  const { data, reload, error, loading } = useAsync(
    () => (employeeId === "" ? Promise.resolve([]) : api.contracts(Number(employeeId))), [employeeId],
  );
  const [start, setStart] = useState(todayIso());
  const [end, setEnd] = useState("");
  const [percent, setPercent] = useState("100");
  const [workDays, setWorkDays] = useState<number[]>(DEFAULT_WORK_DAYS);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (employeeId === "" && employees?.length) setEmp(employees[0].id);
  }, [employees, employeeId]);

  const toggleDay = (day: number, on: boolean) =>
    setWorkDays((xs) => (on ? [...xs, day] : xs.filter((x) => x !== day)));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const found: Record<string, string> = {};
    const pct = Number(percent);
    if (employeeId === "") found.employeeId = "کارمند را انتخاب کنید";
    if (!start) found.start = "تاریخ شروع را وارد کنید";
    if (end && start && end < start) found.end = "پایان نباید قبل از شروع باشد";
    if (!workDays.length) found.workDays = "دست‌کم یک روز کاری انتخاب کنید";
    if (!Number.isFinite(pct) || pct <= 0 || pct > 100) found.contractPercent = "درصد باید بین ۱ تا ۱۰۰ باشد";
    setErrors(found);
    if (Object.keys(found).length || employeeId === "") { focusFirstInvalid(formRef.current); return; }
    setBusy(true);
    try {
      await api.saveContract({ employeeId: Number(employeeId), start, end: end || null, workDays: [...workDays], contractPercent: pct });
      toast("ثبت شد", "success");
      await reload();
    } catch (err) {
      const ae = asApiError(err);
      toast(ae.message, "danger");
      setErrors(camelFields(ae.fields));
    } finally {
      setBusy(false);
    }
  };

  const columns: Column<ContractPeriod>[] = [
    { key: "start", label: "شروع", render: (c) => formatJalaliSafe(c.start) },
    { key: "end", label: "پایان", render: (c) => (c.end ? formatJalaliSafe(c.end) : "نامحدود") },
    { key: "workDays", label: "روزهای کاری", render: (c) => WEEK_ORDER.filter((x) => c.workDays.includes(x)).map((x) => PY_WEEKDAY_LABELS[x]).join("، ") },
    { key: "contractPercent", label: "درصد قرارداد", render: (c) => `${c.contractPercent}٪` },
  ];

  return (
    <>
      <PageHeader title="دوره‌های الگوی کاری" subtitle="بازه هم‌پوشان مجاز نیست" />
      {(error || empError) && <ErrorState message={error ?? empError ?? ""} onRetry={() => void reload()} />}
      <Field label="کارمند">
        <Select value={employeeId} onChange={(e) => setEmp(e.target.value ? Number(e.target.value) : "")}>
          {employees?.map((e) => <option key={e.id} value={e.id}>{e.displayName}</option>)}
        </Select>
      </Field>
      <Card title="دوره جدید">
        <form onSubmit={submit} ref={formRef} noValidate>
          <div className="grid grid-3">
            <Field label="شروع" required error={errors.start}><JalaliDateField value={start} onChange={setStart} /></Field>
            <Field label="پایان (اختیاری)" error={errors.end} hint="خالی = نامحدود">
              <JalaliDateField value={end} min={start || undefined} onChange={setEnd} />
            </Field>
            <Field label="درصد قرارداد" required error={errors.contractPercent}>
              <Input type="number" min={1} max={100} value={percent} dir="ltr" className="ltr-input" onChange={(e) => setPercent(e.target.value)} />
            </Field>
          </div>
          <fieldset className="field-group" aria-describedby={errors.workDays ? "wd-err" : undefined}>
            <legend>روزهای کاری (پیش‌فرض شنبه تا چهارشنبه)</legend>
            <div className="check-row">
              {WEEK_ORDER.map((day) => (
                <Checkbox key={day} label={PY_WEEKDAY_LABELS[day]} checked={workDays.includes(day)} onChange={(e) => toggleDay(day, e.target.checked)} />
              ))}
            </div>
            {errors.workDays && <div className="error" id="wd-err" role="alert">{errors.workDays}</div>}
          </fieldset>
          <Button type="submit" loading={busy} disabled={employeeId === ""}>افزودن دوره</Button>
        </form>
      </Card>
      <div className="mt">
        {loading && !data ? <LoadingBlock /> : <DataTable columns={columns} rows={data ?? []} rowKey={(c) => c.id} caption="دوره‌های الگوی کاری" />}
      </div>
    </>
  );
}
