import { Outlet } from "react-router-dom";

import { ToastProvider } from "./components/Toast";
import { LoginPage } from "./features/auth/LoginPage";
import { AppHeader } from "./features/shell/AppHeader";
import { AuthProvider } from "./hooks/useAuth";
import { useAuthState as useAuth } from "./hooks/useAuthState";

export function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <Gate />
      </ToastProvider>
    </AuthProvider>
  );
}

function Gate() {
  const auth = useAuth();

  if (auth.status === "checking") {
    return <div style={{ padding: 24, color: "var(--text-muted)" }}>세션 확인 중…</div>;
  }
  if (auth.status === "unauthenticated") {
    return <LoginPage />;
  }
  return (
    <div className="app-shell">
      <AppHeader />
      <Outlet />
    </div>
  );
}
