// 백엔드 ActionGroup / ActionItem / ActionRun 응답 모양과 일치.

export type ActionKind = "python" | "argv";

export type ActionRunStatus =
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface ActionRunLogMeta {
  enabled: boolean;
  keep_runs: number;
}

export interface ExternalLogMeta {
  name: string;
  path: string;
}

export interface ActionRun {
  run_id: string;
  group_id: string;
  item_id: string;
  started_at: number;
  ended_at: number | null;
  exit_code: number | null;
  status: ActionRunStatus;
  log_path: string | null;
  cancel_reason: string | null;
  duration_seconds: number | null;
}

export interface ActionItem {
  id: string;
  name: string;
  description: string;
  kind: ActionKind;
  cwd: string | null;
  command: string[];
  env: Record<string, string>;
  log: ActionRunLogMeta;
  external_logs: ExternalLogMeta[];
  // GET /api/actions 응답에만 포함됨.
  recent_runs?: ActionRun[];
}

export interface ActionGroup {
  id: string;
  name: string;
  description: string;
  items: ActionItem[];
}

export interface ActionsListResponse {
  groups: ActionGroup[];
}

export interface ActionRunResponse {
  ok: true;
  run: ActionRun;
}

export interface ActionRunDetailResponse {
  run: ActionRun;
  lines: string[];
  offset: number;
}

export interface ActionRunCancelResponse {
  ok: true;
  run: ActionRun;
}

export interface ActionGroupCreateResponse {
  ok: true;
  group: ActionGroup;
}

export interface ExternalLogTailResponse {
  lines: string[];
  offset: number;
}
