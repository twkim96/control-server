import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useToast } from "../../components/useToast";
import {
  createService,
  deleteService,
  listConfigServices,
  updateService,
} from "../../api/config";
import { listServices } from "../../api/services";
import type { ServicePayload } from "../../types/config";
import type {
  ActionType,
  LifecycleMeta,
  RecoveryMeta,
  RestartMeta,
  ServiceMeta,
} from "../../types/service";
import { describeError } from "../../utils/errors";
import { ActionsField } from "./ActionsField";
import { SUPPORTED_ACTIONS } from "./supported-actions";
import { EnvField } from "./EnvField";
import { LifecycleField } from "./LifecycleField";
import { SupervisionFields } from "./SupervisionFields";
import {
  ServiceFormFields,
  type BasicFields,
} from "./ServiceFormFields";
import classes from "./service-form.module.css";

export type ServiceFormMode = "create" | "edit";

const DEFAULT_LIFECYCLE: LifecycleMeta = {
  mode: "manual",
  autostart: false,
  stop_visibility: "primary",
  restart_visibility: "primary",
  unmanaged_policy: "manage",
};

const DEFAULT_BASIC: BasicFields = {
  id: "",
  name: "",
  description: "",
  cwd: "",
  entry_file: "",
  command: ["python", "-u", "app.py"],
  adopt_command: [],
  adopt_match: "exact",
  port: "",
  port_env_name: "",
  open_url: "",
  https_enabled: false,
  https_cert_file: "",
  https_key_file: "",
  https_enabled_env: "HTTPS",
  https_cert_env: "SSL_CERT_FILE",
  https_key_env: "SSL_KEY_FILE",
  health_enabled: true,
  health_type: "http",
  health_url: "",
  health_timeout: "2",
  health_verify_ssl: true,
  log_tail_lines: "200",
  log_enabled: true,
  log_max_bytes: String(5 * 1024 * 1024),
  log_keep: "3",
  rotate_while_running: false,
  compress_logs: false,
  group: "",
  depends_on: [],
};

const DEFAULT_RESTART: RestartMeta = {
  autorestart: true,
  min_uptime_seconds: 5,
  max_restarts: 5,
  restart_delay_ms: 1000,
  exp_backoff_restart_delay_ms: 100,
  max_memory_restart_mb: 0,
};

const DEFAULT_RECOVERY: RecoveryMeta = {
  enabled: false,
  failure_threshold: 3,
  startup_grace_seconds: 30,
  cooldown_seconds: 300,
  max_restarts_per_hour: 3,
};

const ALL_ACTION_TYPES = new Set<ActionType>(SUPPORTED_ACTIONS.map((a) => a.type));

export function ServiceFormPage({ mode }: { mode: ServiceFormMode }) {
  const navigate = useNavigate();
  const params = useParams<{ id: string }>();
  const toast = useToast();

  const [basic, setBasic] = useState<BasicFields>(DEFAULT_BASIC);
  const [env, setEnv] = useState<Record<string, string>>({});
  const [lifecycle, setLifecycle] = useState<LifecycleMeta>(DEFAULT_LIFECYCLE);
  const [restart, setRestart] = useState<RestartMeta>(DEFAULT_RESTART);
  const [recovery, setRecovery] = useState<RecoveryMeta>(DEFAULT_RECOVERY);
  const [configServices, setConfigServices] = useState<ServiceMeta[]>([]);
  const [supervisorBackend, setSupervisorBackend] = useState<string>();
  const [actions, setActions] = useState<Set<ActionType>>(new Set(ALL_ACTION_TYPES));
  const [loading, setLoading] = useState(mode === "edit");
  const [submitting, setSubmitting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | undefined>();

  const isEdit = mode === "edit";
  const editingId = params.id;

  const applyMeta = useCallback((meta: ServiceMeta) => {
    setBasic({
      id: meta.id,
      name: meta.name,
      description: meta.description,
      cwd: meta.cwd,
      entry_file: meta.entry_file,
      command: meta.command,
      adopt_command: meta.adopt_command ?? [],
      adopt_match: meta.adopt_match,
      port: meta.port == null ? "" : String(meta.port),
      port_env_name: meta.port_env_name ?? "",
      open_url: meta.open_url ?? "",
      https_enabled: meta.https?.enabled ?? meta.open_url?.startsWith("https://") ?? false,
      https_cert_file: meta.https?.cert_file ?? "",
      https_key_file: meta.https?.key_file ?? "",
      https_enabled_env: meta.https?.env.enabled ?? "HTTPS",
      https_cert_env: meta.https?.env.cert_file ?? "SSL_CERT_FILE",
      https_key_env: meta.https?.env.key_file ?? "SSL_KEY_FILE",
      health_enabled: meta.health.enabled,
      health_type: meta.health.type === "tcp" ? "tcp" : "http",
      health_url: meta.health.url ?? "",
      health_timeout: String(meta.health.timeout_seconds),
      health_verify_ssl: meta.health.verify_ssl,
      log_tail_lines: String(meta.log.tail_lines),
      log_enabled: meta.log.enabled,
      log_max_bytes: String(meta.log.max_bytes),
      log_keep: String(meta.log.keep),
      rotate_while_running: meta.log.rotate_while_running ?? false,
      compress_logs: meta.log.compress ?? false,
      group: meta.group ?? "",
      depends_on: [...(meta.depends_on ?? [])],
    });
    setRestart({ ...DEFAULT_RESTART, ...meta.restart });
    setRecovery({ ...DEFAULT_RECOVERY, ...meta.recovery });
    setEnv({ ...meta.env });
    setLifecycle({ ...meta.lifecycle });
    const enabled = new Set<ActionType>();
    for (const a of meta.actions) {
      if (a.enabled) enabled.add(a.type);
    }
    setActions(enabled);
  }, []);

  // edit 모드: 기존 값 로드
  useEffect(() => {
    let cancelled = false;
    let pendingLoads = 2;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSupervisorBackend(undefined);
    const finishLoad = () => {
      pendingLoads -= 1;
      if (!cancelled && pendingLoads === 0 && isEdit) setLoading(false);
    };
    if (isEdit && editingId) {
      setLoading(true);
    }
    listConfigServices()
      .then((res) => {
        if (cancelled) return;
        setConfigServices(res.services);
        if (isEdit && editingId) {
          const found = res.services.find((s) => s.id === editingId);
          if (!found) {
            setError(`서비스를 찾지 못했습니다: ${editingId}`);
            return;
          }
          applyMeta(found);
        }
      })
      .catch((err) => {
        if (!cancelled && isEdit) setError(describeError(err));
      })
      .finally(finishLoad);
    // The config endpoint intentionally contains definitions only. Use the existing
    // service-list capability field to decide whether PM2-only controls are available.
    listServices()
      .then((res) => {
        if (!cancelled) setSupervisorBackend(res.supervisor?.backend);
      })
      .catch(() => {
        if (!cancelled) setSupervisorBackend(undefined);
      })
      .finally(finishLoad);
    return () => {
      cancelled = true;
    };
  }, [editingId, isEdit, applyMeta]);

  const payload = useMemo<ServicePayload | undefined>(() => {
    return buildPayload(basic, env, lifecycle, actions, restart, recovery);
  }, [basic, env, lifecycle, actions, restart, recovery]);

  const validation = useMemo(
    () => validate(basic, restart, recovery),
    [basic, restart, recovery],
  );

  const save = async () => {
    if (!payload || validation) return;
    setSubmitting(true);
    setError(undefined);
    try {
      if (isEdit) {
        await updateService(basic.id, payload);
        toast.push("저장 완료", "success");
      } else {
        await createService(payload);
        toast.push("등록 완료", "success");
      }
      navigate("/");
    } catch (err) {
      setError(describeError(err));
    } finally {
      setSubmitting(false);
    }
  };

  const remove = async () => {
    if (!isEdit || !editingId) return;
    setDeleting(true);
    setError(undefined);
    try {
      await deleteService(editingId);
      toast.push("삭제 완료", "success");
      navigate("/");
    } catch (err) {
      setError(describeError(err));
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  };

  if (loading) {
    return (
      <main className={classes.page}>
        <div className={classes.section}>로딩 중…</div>
      </main>
    );
  }

  return (
    <main className={classes.page}>
      <header className={classes.header}>
        <div>
          <div className={classes.title}>
            {isEdit ? "서버 수정" : "서버 등록"}
          </div>
          <div className={classes.subtitle}>
            {isEdit ? `${editingId}의 설정을 수정합니다` : "새 서비스를 등록합니다"}
          </div>
        </div>
        <Button variant="ghost" onClick={() => navigate("/")}>
          취소
        </Button>
      </header>

      <ServiceFormFields value={basic} onChange={setBasic} lockId={isEdit} />

      {supervisorBackend === "pm2" ? (
        <SupervisionFields
          basic={basic}
          services={configServices}
          restart={restart}
          recovery={recovery}
          onBasicChange={(patch) => setBasic((current) => ({ ...current, ...patch }))}
          onRestartChange={setRestart}
          onRecoveryChange={setRecovery}
          healthEnabled={basic.health_enabled}
        />
      ) : (
        <div className={classes.help} role="note">
          자동 재시작, 헬스 복구, 그룹 및 의존성 설정은 PM2 backend에서만 편집할 수 있습니다.
        </div>
      )}

      <div className={classes.section}>
        <div className={classes.sectionTitle}>환경변수</div>
        <EnvField value={env} onChange={setEnv} />
      </div>

      <div className={classes.section}>
        <div className={classes.sectionTitle}>실행 정책</div>
        <LifecycleField value={lifecycle} onChange={setLifecycle} />
      </div>

      <div className={classes.section}>
        <div className={classes.sectionTitle}>기능</div>
        <ActionsField selected={actions} onChange={setActions} />
      </div>

      {error && <div className={classes.error}>{error}</div>}
      {validation && <div className={classes.error}>{validation}</div>}

      <div className={classes.actions}>
        <div>
          {isEdit && (
            <Button
              variant="danger"
              onClick={() => setConfirmDelete(true)}
              disabled={submitting || deleting}
            >
              삭제
            </Button>
          )}
        </div>
        <div className={classes.actionsRight}>
          <Button variant="ghost" onClick={() => navigate("/")} disabled={submitting}>
            취소
          </Button>
          <Button
            variant="primary"
            onClick={save}
            loading={submitting}
            disabled={!!validation}
          >
            {isEdit ? "저장" : "등록"}
          </Button>
        </div>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title={`${basic.name || basic.id} 삭제`}
        message="이 서비스를 config.yml에서 제거합니다. 실행 중이면 먼저 중지하세요."
        confirmLabel="삭제"
        variant="danger"
        loading={deleting}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={remove}
      />
    </main>
  );
}

function validate(
  basic: BasicFields,
  restart: RestartMeta,
  recovery: RecoveryMeta,
): string | undefined {
  if (!basic.id) return "서비스 ID를 입력하세요.";
  if (!/^[A-Za-z0-9_-]+$/.test(basic.id))
    return "서비스 ID는 영문/숫자/_/- 만 가능합니다.";
  if (!basic.name) return "표시 이름을 입력하세요.";
  if (!basic.cwd) return "working directory를 선택하세요.";
  if (!basic.entry_file) return "실행 파일을 입력하세요.";
  if (basic.command.length === 0) return "실행 명령을 입력하세요.";
  if (basic.port && Number.isNaN(Number(basic.port))) return "포트는 숫자여야 합니다.";
  if (basic.port_env_name && !/^[A-Za-z0-9_]+$/.test(basic.port_env_name))
    return "포트 환경변수 이름은 영문/숫자/_만 가능합니다.";
  if (basic.https_enabled) {
    const envNames = [
      basic.https_enabled_env,
      basic.https_cert_env,
      basic.https_key_env,
    ];
    if (envNames.some((name) => !/^[A-Za-z0-9_]+$/.test(name))) {
      return "HTTPS 환경변수 이름은 영문/숫자/_만 가능합니다.";
    }
  }
  if (basic.health_enabled && !basic.health_url)
    return "health 사용을 켰다면 URL을 입력하세요.";
  if (basic.health_enabled && !["http", "tcp"].includes(basic.health_type))
    return "health 검사 방식은 HTTP 또는 TCP여야 합니다.";
  if (basic.group !== basic.group.trim() || basic.group.length > 80 ||
      basic.group.includes("/") || Array.from(basic.group).some((char) => char.charCodeAt(0) < 32))
    return "그룹 이름은 앞뒤 공백과 /, 제어 문자가 없는 80자 이내 이름이어야 합니다.";
  if (basic.depends_on.includes(basic.id)) return "서비스는 자기 자신에 의존할 수 없습니다.";
  if (!validInteger(basic.log_tail_lines, 0) || !validInteger(basic.log_max_bytes, 0) || !validInteger(basic.log_keep, 0))
    return "로그 줄 수, 회전 크기, 보관 수는 0 이상의 정수여야 합니다.";
  if (!Number.isFinite(restart.min_uptime_seconds) || restart.min_uptime_seconds <= 0)
    return "최소 실행 시간은 0보다 큰 유한한 숫자여야 합니다.";
  if (!Number.isInteger(restart.max_restarts) || restart.max_restarts < 1)
    return "최대 재시작 횟수는 1 이상의 정수여야 합니다.";
  if (![restart.restart_delay_ms, restart.exp_backoff_restart_delay_ms, restart.max_memory_restart_mb]
    .every((value) => Number.isInteger(value) && value >= 0))
    return "재시작 지연과 메모리 제한은 0 이상의 정수여야 합니다.";
  if (!Number.isInteger(recovery.failure_threshold) || recovery.failure_threshold < 1)
    return "연속 실패 횟수는 1 이상의 정수여야 합니다.";
  if (![recovery.startup_grace_seconds, recovery.cooldown_seconds]
    .every((value) => Number.isFinite(value) && value >= 0))
    return "시작 유예 시간과 cooldown은 0 이상의 유한한 숫자여야 합니다.";
  if (!Number.isInteger(recovery.max_restarts_per_hour) || recovery.max_restarts_per_hour < 1)
    return "시간당 최대 재시작 횟수는 1 이상의 정수여야 합니다.";
  if (recovery.max_restarts_per_hour > 100)
    return "시간당 최대 재시작 횟수는 100 이하여야 합니다.";
  if (recovery.enabled && !basic.health_enabled)
    return "헬스 복구를 사용하려면 health endpoint를 켜야 합니다.";
  return undefined;
}

function validInteger(value: string, minimum: number): boolean {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= minimum;
}

function buildPayload(
  basic: BasicFields,
  env: Record<string, string>,
  lifecycle: LifecycleMeta,
  actions: Set<ActionType>,
  restart: RestartMeta,
  recovery: RecoveryMeta,
): ServicePayload | undefined {
  const port = basic.port.trim() ? Number(basic.port) : null;

  const actionList: ServicePayload["actions"] = SUPPORTED_ACTIONS
    .filter((a) => actions.has(a.type))
    .map((a) => {
      if (a.type === "process_stop") {
        return {
          id: a.id,
          label: a.label,
          type: a.type,
          enabled: true,
          strategy: {
            signal: "SIGINT",
            timeout_seconds: lifecycle.mode === "always_on" ? 60 : 5,
            confirm_required: lifecycle.mode === "always_on",
            fallback: ["SIGTERM", "SIGKILL"],
          },
        };
      }
      return {
        id: a.id,
        label: a.label,
        type: a.type,
        enabled: true,
      };
    });

  return {
    id: basic.id,
    name: basic.name,
    description: basic.description || undefined,
    cwd: basic.cwd,
    entry_file: basic.entry_file,
    command: basic.command,
    adopt_command: basic.adopt_command.length > 0 ? basic.adopt_command : null,
    adopt_match: basic.adopt_match,
    env,
    port,
    port_env_name: basic.port_env_name.trim() || null,
    open_url: basic.open_url || null,
    https: {
      enabled: basic.https_enabled,
      cert_file: basic.https_cert_file.trim() || null,
      key_file: basic.https_key_file.trim() || null,
      env: {
        enabled: basic.https_enabled_env.trim() || "HTTPS",
        cert_file: basic.https_cert_env.trim() || "SSL_CERT_FILE",
        key_file: basic.https_key_env.trim() || "SSL_KEY_FILE",
      },
    },
    health: {
      enabled: basic.health_enabled,
      type: basic.health_enabled ? basic.health_type : "none",
      url: basic.health_enabled ? basic.health_url : null,
      timeout_seconds: Number(basic.health_timeout) || 2,
      verify_ssl: basic.health_verify_ssl,
    },
    log: {
      enabled: basic.log_enabled,
      tail_lines: Number(basic.log_tail_lines),
      max_bytes: Number(basic.log_max_bytes),
      keep: Number(basic.log_keep),
      rotate_while_running: basic.rotate_while_running,
      compress: basic.compress_logs,
    },
    restart,
    recovery,
    group: basic.group.trim(),
    depends_on: basic.depends_on,
    lifecycle,
    actions: actionList,
  };
}
