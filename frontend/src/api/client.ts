// 모든 백엔드 호출의 공통 래퍼.
//
// Phase 4 인증:
// - 세션 쿠키는 자동 (credentials: "same-origin").
// - mutation 호출에는 X-CSRF-Token 헤더를 부착한다.
// - csrf 토큰은 useAuth 훅이 메모리에 보관하고 setCsrfToken으로 주입.
// - SSE는 hooks/useLogStream에서 쿠키만으로 인증.

import type { ApiError } from "../types/api";

export interface RequestOptions extends RequestInit {
  parse?: "json" | "text" | "none";
}

export class ApiRequestError extends Error {
  status: number;
  body: ApiError | string | null;

  constructor(status: number, message: string, body: ApiError | string | null) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

const DEFAULT_BASE = "";

function getBaseUrl(): string {
  return import.meta.env.VITE_API_BASE || DEFAULT_BASE;
}

// CSRF 토큰은 useAuth가 setCsrfToken으로 등록한다.
let csrfToken: string | undefined;

export function setCsrfToken(value: string | undefined): void {
  csrfToken = value;
}

export function getCsrfToken(): string | undefined {
  return csrfToken;
}

// 401 응답을 받았을 때 호출되는 핸들러. useAuth가 등록한다.
let onUnauthorized: (() => void) | undefined;

export function setUnauthorizedHandler(handler: (() => void) | undefined): void {
  onUnauthorized = handler;
}

function isMutation(method: string | undefined): boolean {
  if (!method) return false;
  return ["POST", "PUT", "PATCH", "DELETE"].includes(method.toUpperCase());
}

export async function apiFetch<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { parse = "json", headers, ...rest } = options;
  const url = `${getBaseUrl()}${path}`;

  const finalHeaders = new Headers(headers);
  if (rest.body && !finalHeaders.has("Content-Type")) {
    finalHeaders.set("Content-Type", "application/json");
  }
  finalHeaders.set("Accept", finalHeaders.get("Accept") ?? "application/json");

  if (isMutation(rest.method) && csrfToken) {
    finalHeaders.set("X-CSRF-Token", csrfToken);
  }

  const response = await fetch(url, {
    ...rest,
    headers: finalHeaders,
    credentials: "same-origin",
  });

  if (response.status === 401) {
    onUnauthorized?.();
  }

  if (!response.ok) {
    const body = await readSafely(response);
    const message = extractErrorMessage(body) ?? `HTTP ${response.status}`;
    throw new ApiRequestError(response.status, message, body);
  }

  if (parse === "none") {
    // @ts-expect-error - 호출자가 void를 기대하면 unknown으로 둬도 안전.
    return undefined;
  }
  if (parse === "text") {
    return (await response.text()) as unknown as T;
  }
  return (await response.json()) as T;
}

async function readSafely(response: Response): Promise<ApiError | string | null> {
  const contentType = response.headers.get("Content-Type") ?? "";
  try {
    if (contentType.includes("application/json")) {
      return (await response.json()) as ApiError;
    }
    return await response.text();
  } catch {
    return null;
  }
}

function extractErrorMessage(body: ApiError | string | null): string | undefined {
  if (!body) return undefined;
  if (typeof body === "string") return body;
  if (typeof body.message === "string") return body.message;
  if (typeof body.error === "string") return body.error;
  return undefined;
}

export const apiBaseUrl = getBaseUrl;
