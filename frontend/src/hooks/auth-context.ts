import { createContext } from "react";

export interface AuthState {
  status: "checking" | "authenticated" | "unauthenticated";
  user: string | undefined;
  error: string | undefined;
  login: (password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

export const AuthContext = createContext<AuthState | null>(null);
