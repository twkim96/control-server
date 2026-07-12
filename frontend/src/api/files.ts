import { apiFetch } from "./client";
import type { FileListResponse } from "../types/config";

export function listFiles(path?: string): Promise<FileListResponse> {
  const qs = path ? `?path=${encodeURIComponent(path)}` : "";
  return apiFetch<FileListResponse>(`/api/files${qs}`);
}
