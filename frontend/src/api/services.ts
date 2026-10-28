import { apiFetch } from "./client";
import type {
  ActionResponse,
  LogTailResponse,
  ServicesResponse,
  ServiceMeta,
  ServiceEventsResponse,
  ServiceGroupAction,
  ServiceGroupJobResponse,
} from "../types/service";

export function listServices(): Promise<ServicesResponse> {
  return apiFetch<ServicesResponse>("/api/services");
}

export function getService(id: string): Promise<ServiceMeta> {
  return apiFetch<ServiceMeta>(`/api/services/${encodeURIComponent(id)}`);
}

export function cancelRecovery(id: string): Promise<ActionResponse> {
  return apiFetch<ActionResponse>(
    `/api/services/${encodeURIComponent(id)}/cancel_recovery`,
    { method: "POST" },
  );
}

export function getServiceEvents(id: string): Promise<ServiceEventsResponse> {
  return apiFetch<ServiceEventsResponse>(
    `/api/services/${encodeURIComponent(id)}/events`,
  );
}

export function startServiceGroupAction(
  group: string,
  action: ServiceGroupAction,
): Promise<ServiceGroupJobResponse> {
  return apiFetch<ServiceGroupJobResponse>(
    `/api/service-groups/${encodeURIComponent(group)}/actions/${action}`,
    { method: "POST" },
  );
}

export function getServiceGroupOperationJob(
  jobId: string,
): Promise<ServiceGroupJobResponse> {
  return apiFetch<ServiceGroupJobResponse>(
    `/api/service-operations/jobs/${encodeURIComponent(jobId)}`,
  );
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

export function takeOverExternal(
  serviceId: string,
  identity: { pid: number; created_at: number },
): Promise<{ ok: true; replaced: { pid: number }; service: ServiceMeta }> {
  return apiFetch(`/api/services/${encodeURIComponent(serviceId)}/take_over`, {
    method: "POST",
    body: JSON.stringify(identity),
  });
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
