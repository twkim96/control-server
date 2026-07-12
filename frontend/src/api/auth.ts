import { apiFetch } from "./client";
import type { MetaResponse } from "../types/api";

export interface LoginResponse {
  ok: true;
  user: string;
  csrf_token: string;
}

export interface MeResponse {
  authenticated: boolean;
  user?: string;
  csrf_token?: string;
}

export function getMeta(): Promise<MetaResponse> {
  return apiFetch<MetaResponse>("/api/meta");
}

export function getMe(): Promise<MeResponse> {
  return apiFetch<MeResponse>("/api/auth/me");
}

export function login(password: string): Promise<LoginResponse> {
  return apiFetch<LoginResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export function logout(): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>("/api/auth/logout", { method: "POST" });
}
