import { useState, type FormEvent } from "react";

import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { useAuthState as useAuth } from "../../hooks/useAuthState";
import classes from "./auth.module.css";

export function LoginPage() {
  const auth = useAuth();
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (auth.status === "checking") {
    return (
      <div className={classes.shell}>
        <div className={classes.card}>
          <div className={classes.checking}>세션 확인 중…</div>
        </div>
      </div>
    );
  }

  const onSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await auth.login(password);
    } catch {
      // useAuth가 error 메시지 보관
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={classes.shell}>
      <form className={classes.card} onSubmit={onSubmit}>
        <div>
          <div className={classes.title}>Server Control</div>
          <div className={classes.subtitle}>로그인</div>
        </div>

        <div>
          <label htmlFor="password" className={classes.label}>
            비밀번호
          </label>
          <Input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
            autoComplete="current-password"
            required
          />
        </div>

        {auth.error && <div className={classes.error}>{auth.error}</div>}

        <div className={classes.actions}>
          <Button
            type="submit"
            variant="primary"
            loading={submitting}
            disabled={!password}
          >
            로그인
          </Button>
        </div>
      </form>
    </div>
  );
}
