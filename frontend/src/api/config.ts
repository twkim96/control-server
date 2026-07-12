import { apiFetch } from "./client";
import type { ServicePayload } from "../types/config";
import type { ServicesResponse } from "../types/service";

export function listConfigServices(): Promise<ServicesResponse> {
  return apiFetch<ServicesResponse>("/api/config/services");
}

export function createService(payload: ServicePayload) {
  return apiFetch<{ ok: true; service: ServicePayload }>(
    "/api/config/services",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export function updateService(id: string, payload: ServicePayload) {
  return apiFetch<{ ok: true; service: ServicePayload }>(
    `/api/config/services/${encodeURIComponent(id)}`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    },
  );
}

export function deleteService(id: string) {
  return apiFetch<{ ok: true }>(
    `/api/config/services/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}

export function reloadConfig() {
  return apiFetch<{ ok: true }>("/api/config/reload", { method: "POST" });
}

export function reorderServices(order: string[]) {
  return apiFetch<{ ok: true }>("/api/config/services/reorder", {
    method: "POST",
    body: JSON.stringify({ order }),
  });
}
