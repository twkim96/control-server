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
import type { ServicePayload } from "../../types/config";
import type { ActionType, LifecycleMeta, ServiceMeta } from "../../types/service";
import { describeError } from "../../utils/errors";
import { ActionsField } from "./ActionsField";
import { SUPPORTED_ACTIONS } from "./supported-actions";
import { EnvField } from "./EnvField";
import { LifecycleField } from "./LifecycleField";
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
  health_url: "",
  health_timeout: "2",
  health_verify_ssl: true,
  log_tail_lines: "200",
};

const ALL_ACTION_TYPES = new Set<ActionType>(SUPPORTED_ACTIONS.map((a) => a.type));

export function ServiceFormPage({ mode }: { mode: ServiceFormMode }) {
  const navigate = useNavigate();
  const params = useParams<{ id: string }>();
  const toast = useToast();

  const [basic, setBasic] = useState<BasicFields>(DEFAULT_BASIC);
  const [env, setEnv] = useState<Record<string, string>>({});
  const [lifecycle, setLifecycle] = useState<LifecycleMeta>(DEFAULT_LIFECYCLE);
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
      health_url: meta.health.url ?? "",
      health_timeout: String(meta.health.timeout_seconds),
      health_verify_ssl: meta.health.verify_ssl,
      log_tail_lines: String(meta.log.tail_lines),
    });
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
    if (!isEdit || !editingId) return;
    let cancelled = false;
    // 비동기 fetch 직전 loading 표시. then/finally에서 다시 setState한다.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    listConfigServices()
      .then((res) => {
        if (cancelled) return;
        const found = res.services.find((s) => s.id === editingId);
        if (!found) {
          setError(`서비스를 찾지 못했습니다: ${editingId}`);
          return;
        }
        applyMeta(found);
      })
      .catch((err) => !cancelled && setError(describeError(err)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [editingId, isEdit, applyMeta]);

  const payload = useMemo<ServicePayload | undefined>(() => {
    return buildPayload(basic, env, lifecycle, actions);
  }, [basic, env, lifecycle, actions]);

  const validation = useMemo(() => validate(basic), [basic]);

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

function validate(basic: BasicFields): string | undefined {
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
  return undefined;
}

function buildPayload(
  basic: BasicFields,
  env: Record<string, string>,
  lifecycle: LifecycleMeta,
  actions: Set<ActionType>,
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
      type: basic.health_enabled ? "http" : "none",
      url: basic.health_enabled ? basic.health_url : null,
      timeout_seconds: Number(basic.health_timeout) || 2,
      verify_ssl: basic.health_verify_ssl,
    },
    log: {
      enabled: true,
      tail_lines: Number(basic.log_tail_lines) || 200,
      max_bytes: 5 * 1024 * 1024,
      keep: 3,
    },
    lifecycle,
    actions: actionList,
  };
}
