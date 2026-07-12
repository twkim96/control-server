import { apiFetch } from "./client";
import type {
  ActionGroup,
  ActionGroupCreateResponse,
  ActionRunCancelResponse,
  ActionRunDetailResponse,
  ActionRunResponse,
  ActionsListResponse,
  ExternalLogTailResponse,
} from "../types/action";

export function listActions(): Promise<ActionsListResponse> {
  return apiFetch<ActionsListResponse>("/api/actions");
}

export function runAction(
  groupId: string,
  itemId: string,
): Promise<ActionRunResponse> {
  return apiFetch<ActionRunResponse>(
    `/api/actions/${encodeURIComponent(groupId)}/${encodeURIComponent(itemId)}/run`,
    { method: "POST" },
  );
}

export function getRun(
  runId: string,
  options: { tail?: number } = {},
): Promise<ActionRunDetailResponse> {
  const params = new URLSearchParams();
  if (typeof options.tail === "number") {
    params.set("tail", String(options.tail));
  }
  const qs = params.toString();
  return apiFetch<ActionRunDetailResponse>(
    `/api/actions/runs/${encodeURIComponent(runId)}${qs ? `?${qs}` : ""}`,
  );
}

export function cancelRun(runId: string): Promise<ActionRunCancelResponse> {
  return apiFetch<ActionRunCancelResponse>(
    `/api/actions/runs/${encodeURIComponent(runId)}/cancel`,
    { method: "POST" },
  );
}

export function createActionGroup(
  payload: Omit<ActionGroup, "items"> & { items: ActionGroup["items"] },
): Promise<ActionGroupCreateResponse> {
  return apiFetch<ActionGroupCreateResponse>("/api/config/actions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateActionGroup(
  id: string,
  payload: ActionGroup,
): Promise<ActionGroupCreateResponse> {
  return apiFetch<ActionGroupCreateResponse>(
    `/api/config/actions/${encodeURIComponent(id)}`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    },
  );
}

export function deleteActionGroup(id: string): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>(
    `/api/config/actions/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}

export function tailExternalLog(
  groupId: string,
  itemId: string,
  path: string,
  options: { tail?: number } = {},
): Promise<ExternalLogTailResponse> {
  const params = new URLSearchParams({
    group_id: groupId,
    item_id: itemId,
    path,
  });
  if (typeof options.tail === "number") {
    params.set("tail", String(options.tail));
  }
  return apiFetch<ExternalLogTailResponse>(
    `/api/actions/external_log?${params.toString()}`,
  );
}

export function reorderActionGroups(order: string[]): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>("/api/config/actions/reorder", {
    method: "POST",
    body: JSON.stringify({ order }),
  });
}
