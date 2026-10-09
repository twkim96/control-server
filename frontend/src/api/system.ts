import { apiFetch } from "./client";
import type { RuntimeResource } from "../types/service";

export interface PythonInterpreterMeta {
  path: string;
  version: string;
  note: string;
}

export interface PythonInterpretersResponse {
  interpreters: PythonInterpreterMeta[];
}

export interface ControllerResourceResponse {
  resource: RuntimeResource;
}

export function listPythonInterpreters(): Promise<PythonInterpretersResponse> {
  return apiFetch<PythonInterpretersResponse>("/api/system/python_interpreters");
}

export function getControllerResource(): Promise<ControllerResourceResponse> {
  return apiFetch<ControllerResourceResponse>("/api/system/controller_resource");
}

export interface Pm2EngineStatus {
  supported: boolean;
  reason?: string;
  current_version: string | null;
  daemon_version: string | null;
  latest_version: string | null;
  update_available: boolean;
  checked_at: string | null;
  job: null | {
    id: string;
    status: "running" | "succeeded" | "failed";
    phase: string;
    message: string;
    error?: string;
    started_at: string;
    finished_at?: string;
    from_version?: string;
    target_version?: string;
    rolled_back?: boolean;
  };
}

export function getPm2EngineStatus(): Promise<Pm2EngineStatus> {
  return apiFetch<Pm2EngineStatus>("/api/system/pm2");
}

export function checkPm2EngineUpdate(): Promise<Pm2EngineStatus> {
  return apiFetch<Pm2EngineStatus>("/api/system/pm2/check", { method: "POST" });
}

export function updatePm2Engine(): Promise<Pm2EngineStatus> {
  return apiFetch<Pm2EngineStatus>("/api/system/pm2/update", { method: "POST" });
}
