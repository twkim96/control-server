// 백엔드 service_registry.service_to_meta() 결과와 동일한 모양.
// + routes/api.py가 합쳐 보내는 runtime 부분을 함께 정의.

export type ServiceStatePhase =
  | "running"
  | "running_external"
  | "unhealthy"
  | "stopped"
  | "starting"
  | "stopping"
  | "unknown";

export type LifecycleMode = "manual" | "always_on";
export type Visibility = "primary" | "danger_menu" | "hidden";
export type UnmanagedPolicy = "status_only" | "manage";
export type AdoptMatch = "exact" | "prefix";

export type ActionType =
  | "process_start"
  | "process_stop"
  | "process_restart"
  | "health_check"
  | "show_logs"
  | "open_url"
  | "edit_config";

export interface StopStrategyMeta {
  signal: string;
  timeout_seconds: number;
  confirm_required: boolean;
  fallback: string[];
}

export interface ActionMeta {
  id: string;
  label: string;
  type: ActionType;
  enabled: boolean;
  strategy?: StopStrategyMeta;
}

export interface HealthMeta {
  enabled: boolean;
  type: string;
  url: string | null;
  timeout_seconds: number;
  verify_ssl: boolean;
}

export interface HttpsMeta {
  enabled: boolean;
  cert_file: string | null;
  key_file: string | null;
  env: {
    enabled: string;
    cert_file: string;
    key_file: string;
  };
}

export interface LogMeta {
  enabled: boolean;
  tail_lines: number;
  max_bytes: number;
  keep: number;
}

export interface LifecycleMeta {
  mode: LifecycleMode;
  autostart: boolean;
  stop_visibility: Visibility;
  restart_visibility: Visibility;
  unmanaged_policy: UnmanagedPolicy;
}

export interface RuntimeHealth {
  enabled: boolean;
  ok: boolean | null;
  reason?: string;
  url?: string;
  status_code?: number;
  elapsed_seconds?: number;
}

export interface RuntimePortCheck {
  port: number;
  open: boolean;
  errno: number | null;
}

export type ResourceReason =
  | "ok"
  | "not_running"
  | "pid_reused"
  | "access_denied"
  | "no_such_process";

export interface RuntimeResource {
  available: boolean;
  reason: ResourceReason;
  pid: number | null;
  sampled_at: number;
  cpu_percent: number | null;
  memory_rss_bytes: number | null;
  process_count: number;
  children_count: number;
}

// 입양(adoption) 실패 사유. running_external 상태의 상세 응답에만 포함된다.
// 백엔드 process_manager.AdoptDiagnostics와 1:1.
export type AdoptReason =
  | "ok"
  | "policy_not_manage"
  | "no_port"
  | "no_port_holder"
  | "already_tracked"
  | "pid_is_self"
  | "pid_tracked_by_other"
  | "proc_inspect_failed"
  | "cmdline_mismatch"
  | "cwd_resolve_failed"
  | "cwd_mismatch"
  | "health_failed"
  | "pm2_exclusive";

export interface AdoptDiagnostics {
  ok: boolean;
  reason: AdoptReason;
  candidate_pid: number | null;
  expected_command: string[] | null;
  adopt_command: string[] | null;
  actual_cmdline: string[] | null;
  cwd_expected: string | null;
  cwd_actual: string | null;
  health_ok: boolean | null;
}

export interface RuntimeInfo {
  state: ServiceStatePhase;
  alive: boolean;
  unmanaged: boolean;
  unmanaged_pid: number | null;
  pid: number | null;
  pgid: number | null;
  uptime_seconds: number | null;
  health: RuntimeHealth;
  port_check: RuntimePortCheck | null;
  last_exit_code: number | null;
  last_exit_time: number | null;
  resource: RuntimeResource;
  // running_external 상태의 상세(GET /api/services/<id>) 응답에만 포함.
  adopt_diagnostics?: AdoptDiagnostics;
}

export interface ServiceMeta {
  id: string;
  name: string;
  description: string;
  cwd: string;
  entry_file: string;
  command: string[];
  adopt_command: string[] | null;
  adopt_match: AdoptMatch;
  env: Record<string, string>;
  port: number | null;
  port_env_name: string | null;
  open_url: string | null;
  https: HttpsMeta;
  health: HealthMeta;
  log: LogMeta;
  lifecycle: LifecycleMeta;
  actions: ActionMeta[];
  // GET /api/services 와 GET /api/services/<id> 응답에 함께 포함된다.
  runtime?: RuntimeInfo;
}

export interface SupervisorDiagnostics {
  backend: "pm2";
  degraded: boolean;
  refreshing: boolean;
  snapshot_age_seconds: number | null;
  last_error: string | null;
  restart_required_service_ids: string[];
  orphan_service_ids: string[];
}

export interface ServicesResponse {
  services: ServiceMeta[];
  supervisor?: SupervisorDiagnostics;
}

export interface ActionResponse {
  ok: true;
  service: ServiceMeta;
}

export interface LogTailResponse {
  lines: string[];
  offset: number;
}
