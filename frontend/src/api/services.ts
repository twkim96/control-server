import { apiFetch } from "./client";
import type {
  ActionResponse,
  LogTailResponse,
  ServicesResponse,
  ServiceMeta,
} from "../types/service";

export function listServices(): Promise<ServicesResponse> {
  return apiFetch<ServicesResponse>("/api/services");
}

export function getService(id: string): Promise<ServiceMeta> {
  return apiFetch<ServiceMeta>(`/api/services/${encodeURIComponent(id)}`);
}

export function triggerAction(
  serviceId: string,
  actionId: string,
): Promise<ActionResponse> {
  return apiFetch<ActionResponse>(
    `/api/services/${encodeURIComponent(serviceId)}/actions/${encodeURIComponent(actionId)}`,
    { method: "POST" },
  );
}

export interface KilledExternal {
  pid: number;
  name: string;
  signal: string;
  duration_seconds: number;
}

export interface KillExternalResponse {
  ok: true;
  killed: KilledExternal;
  service: ServiceMeta;
}

export function killExternal(serviceId: string): Promise<KillExternalResponse> {
  return apiFetch<KillExternalResponse>(
    `/api/services/${encodeURIComponent(serviceId)}/kill_external`,
    { method: "POST" },
  );
}

export function tailLogs(
  serviceId: string,
  options: { tail?: number; sinceOffset?: number } = {},
): Promise<LogTailResponse> {
  const params = new URLSearchParams();
  if (typeof options.sinceOffset === "number") {
    params.set("since_offset", String(options.sinceOffset));
  } else if (typeof options.tail === "number") {
    params.set("tail", String(options.tail));
  }
  const qs = params.toString();
  return apiFetch<LogTailResponse>(
    `/api/services/${encodeURIComponent(serviceId)}/logs${qs ? `?${qs}` : ""}`,
  );
}
