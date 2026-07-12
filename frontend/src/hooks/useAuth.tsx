import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { getMe, login as apiLogin, logout as apiLogout } from "../api/auth";
import { setCsrfToken, setUnauthorizedHandler } from "../api/client";
import { describeError } from "../utils/errors";
import { AuthContext, type AuthState } from "./auth-context";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthState["status"]>("checking");
  const [user, setUser] = useState<string | undefined>();
  const [error, setError] = useState<string | undefined>();
  const tokenRef = useRef<string | undefined>(undefined);

  const applyAuth = useCallback(
    (authed: boolean, csrf: string | undefined, name: string | undefined) => {
      if (authed && csrf) {
        tokenRef.current = csrf;
        setCsrfToken(csrf);
        setUser(name);
        setStatus("authenticated");
      } else {
        tokenRef.current = undefined;
        setCsrfToken(undefined);
        setUser(undefined);
        setStatus("unauthenticated");
      }
    },
    [],
  );

  const refresh = useCallback(async () => {
    try {
      const me = await getMe();
      applyAuth(me.authenticated, me.csrf_token, me.user);
      setError(undefined);
    } catch (err) {
      applyAuth(false, undefined, undefined);
      setError(describeError(err));
    }
  }, [applyAuth]);

  const login = useCallback(
    async (password: string) => {
      try {
        const res = await apiLogin(password);
        applyAuth(true, res.csrf_token, res.user);
        setError(undefined);
      } catch (err) {
        applyAuth(false, undefined, undefined);
        const message = describeError(err);
        setError(message);
        throw err;
      }
    },
    [applyAuth],
  );

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } catch {
      // 무시: 어차피 클라 상태는 비울 거니까
    }
    applyAuth(false, undefined, undefined);
  }, [applyAuth]);

  // 401을 받으면 자동으로 unauthenticated 상태로.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      applyAuth(false, undefined, undefined);
    });
    return () => setUnauthorizedHandler(undefined);
  }, [applyAuth]);

  // 처음 마운트 시 세션 상태 조회.
  useEffect(() => {
    // setState-in-effect: refresh 호출 결과는 비동기 setState. 마운트 시 1회만 발생.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  const value = useMemo<AuthState>(
    () => ({ status, user, error, login, logout, refresh }),
    [status, user, error, login, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
