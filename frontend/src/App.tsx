import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Layout } from "./components/Layout";
import { LoadingBlock } from "./components/ui";
import { hasAny, type Role } from "./lib/roles";
import { AuditPage, JobsPage, NotFoundPage, NotificationsPage, ProfilePage, SettingsPage } from "./pages/AdminPages";
import { CalendarPage, CoveragePage, TimelinePage } from "./pages/CalendarPages";
import { DashboardPage } from "./pages/DashboardPage";
import { BradfordPage } from "./pages/BradfordPage";
import { ActivateAccountPage } from "./pages/ActivateAccountPage";
import { BalancesPage, EntitlementsPage, ImportPage, LeaveTypesPage, ReportsPage } from "./pages/HrPages";
import { LoginPage } from "./pages/LoginPage";
import { ContractsPage, EmployeesPage, HolidaysPage, OrganizationPage } from "./pages/OrgPages";
import { HrAbsencePage, RequestDetailPage, RequestNewPage, RequestsPage } from "./pages/RequestsPages";
import { AuthProvider, useAuth } from "./state/Auth";
import { ThemeProvider } from "./state/Theme";
import { NotificationsProvider } from "./state/Notifications";
import { ToastProvider } from "./state/Toast";

function Guard({ children, roles }: { children: ReactNode; roles?: Role[] }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) {
    return (
      <div style={{ padding: 24 }}>
        <LoadingBlock label="در حال بارگذاری نشست…" />
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />;
  if (roles && !hasAny(user.roles, ...roles)) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/activate-account" element={<ActivateAccountPage />} />
      <Route element={<Guard><Layout /></Guard>}>
        <Route index element={<DashboardPage />} />
        <Route path="notifications" element={<NotificationsPage />} />
        <Route path="profile" element={<ProfilePage />} />
        <Route path="requests" element={<Guard roles={["employee", "manager", "hr"]}><RequestsPage /></Guard>} />
        <Route path="requests/new" element={<Guard roles={["employee", "manager", "hr"]}><RequestNewPage /></Guard>} />
        <Route path="requests/:id" element={<Guard roles={["employee", "manager", "hr"]}><RequestDetailPage /></Guard>} />
        <Route path="calendar" element={<CalendarPage />} />
        <Route path="timeline" element={<TimelinePage />} />
        <Route path="balances" element={<Guard roles={["employee", "manager", "hr", "exec"]}><BalancesPage /></Guard>} />
        <Route path="coverage" element={<Guard roles={["manager", "hr"]}><CoveragePage /></Guard>} />
        <Route path="hr/absences" element={<Guard roles={["hr"]}><HrAbsencePage /></Guard>} />
        <Route path="hr/entitlements" element={<Guard roles={["hr"]}><EntitlementsPage /></Guard>} />
        <Route path="hr/bradford" element={<Guard roles={["hr"]}><BradfordPage /></Guard>} />
        <Route path="hr/import" element={<Guard roles={["hr"]}><ImportPage /></Guard>} />
        <Route path="hr/contracts" element={<Guard roles={["hr"]}><ContractsPage /></Guard>} />
        <Route path="leave-types" element={<Guard roles={["hr", "admin"]}><LeaveTypesPage /></Guard>} />
        <Route path="reports" element={<Guard roles={["hr", "exec"]}><ReportsPage /></Guard>} />
        <Route path="employees" element={<Guard roles={["hr", "admin", "manager"]}><EmployeesPage /></Guard>} />
        <Route path="organization" element={<Guard roles={["admin", "hr"]}><OrganizationPage /></Guard>} />
        <Route path="holidays" element={<HolidaysPage />} />
        <Route path="settings" element={<Guard roles={["admin"]}><SettingsPage /></Guard>} />
        <Route path="jobs" element={<Guard roles={["admin"]}><JobsPage /></Guard>} />
        <Route path="audit" element={<Guard roles={["admin", "hr"]}><AuditPage /></Guard>} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <ErrorBoundary fullPage>
      <ThemeProvider>
        <AuthProvider>
          <ToastProvider>
            <BrowserRouter>
              <NotificationsProvider>
                <AppRoutes />
              </NotificationsProvider>
            </BrowserRouter>
          </ToastProvider>
        </AuthProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}
