import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { errorMessage } from "../api/errors";
import { Banner, Button, Card, Field, Input, PageHeader } from "../components/ui";
import { consumeActivationUrl } from "../lib/activation";

export function ActivateAccountPage() {
  const [token, setToken] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const activation = consumeActivationUrl(window.location.href);
    window.history.replaceState(window.history.state, document.title, activation.cleanUrl);
    setToken(activation.token);
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || !token) return;
    if (password !== confirmation) {
      setError("رمز عبور و تکرار آن یکسان نیستند");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.activateAccount(token, email.trim(), password);
      setToken(null);
      setPassword("");
      setConfirmation("");
      setSuccess(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <Card>
        <div className="login-card">
          <PageHeader title="فعال‌سازی حساب" subtitle="برای فعال‌کردن حساب، ایمیل دعوت و گذرواژه را وارد کنید." />
          {success ? (
            <>
              <Banner tone="info">حساب فعال شد. برای ادامه با گذرواژه جدید وارد شوید.</Banner>
              <Link className="btn primary" to="/login">رفتن به ورود</Link>
            </>
          ) : !token ? (
            <>
              <Banner tone="danger">پیوند فعال‌سازی معتبر نیست یا توکن آن وجود ندارد. از منابع انسانی درخواست دعوت تازه کنید.</Banner>
              <Link className="btn secondary" to="/login">رفتن به ورود</Link>
            </>
          ) : (
            <>
              {error && <Banner tone="danger">{error}</Banner>}
              <form onSubmit={submit} noValidate>
                <Field label="ایمیل دعوت" required>
                  <Input
                    type="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    autoComplete="email"
                    autoCapitalize="none"
                    dir="ltr"
                    className="ltr-input"
                    required
                  />
                </Field>
                <Field label="گذرواژه جدید" required hint="حداقل طول و سایر قواعد در سرور بررسی می‌شوند.">
                  <Input
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    autoComplete="new-password"
                    dir="ltr"
                    className="ltr-input"
                    required
                  />
                </Field>
                <Field label="تکرار گذرواژه" required>
                  <Input
                    type="password"
                    value={confirmation}
                    onChange={(event) => setConfirmation(event.target.value)}
                    autoComplete="new-password"
                    dir="ltr"
                    className="ltr-input"
                    required
                  />
                </Field>
                <Button
                  type="submit"
                  loading={busy}
                  disabled={!email.trim() || !password || !confirmation}
                  style={{ width: "100%" }}
                >
                  فعال‌سازی
                </Button>
              </form>
            </>
          )}
        </div>
      </Card>
    </div>
  );
}
