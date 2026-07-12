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
