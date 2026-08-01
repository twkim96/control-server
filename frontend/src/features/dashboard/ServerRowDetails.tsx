import { useEffect, useMemo, useState } from "react";

import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useToast } from "../../components/useToast";
import { useAction } from "../../hooks/useAction";
import { getService, killExternal } from "../../api/services";
import type { ActionMeta, ActionType, AdoptDiagnostics, ServiceMeta } from "../../types/service";
import { describeError } from "../../utils/errors";
import {
  formatCommand,
  formatCpuPercent,
  formatEnv,
  formatExitCode,
  formatMemory,
  formatPid,
  formatPort,
  RESOURCE_USAGE_HELP,
} from "../../utils/format";
import { openServiceUrl } from "../../utils/openServiceUrl";
import { formatRelative, formatTime, formatUptime } from "../../utils/time";
import { ExternalKillDialog } from "./ExternalKillDialog";
import { StatusDot } from "./StatusDot";
import { getActionConfirmCopy, shouldConfirmAction } from "./actionConfirm";
import classes from "./dashboard.module.css";

export interface ServerRowDetailsProps {
  service: ServiceMeta;
  onMutated: () => void;
  onOpenLogs: () => void;
}

// 펼침 패널의 직접 노출용 액션 (logs/stop/restart는 별도 처리).
// stop과 restart는 lifecycle.{stop,restart}_visibility 에 따라 primary/danger_menu/hidden 분기.
const PRIMARY_ACTION_ORDER: ActionType[] = [
  "process_start",
  "health_check",
  "open_url",
];

export function ServerRowDetails({ service, onMutated, onOpenLogs }: ServerRowDetailsProps) {
  const { pendingActionId, run } = useAction();
  const toast = useToast();
  const [confirm, setConfirm] = useState<ActionMeta | undefined>();
  const [killOpen, setKillOpen] = useState(false);
  const [killing, setKilling] = useState(false);
  const [adoptDiagnostics, setAdoptDiagnostics] = useState<AdoptDiagnostics | undefined>();
  const [diagnosticsError, setDiagnosticsError] = useState<string | undefined>();

  const isExternal = service.runtime?.state === "running_external";
  const externalPid = service.runtime?.unmanaged_pid ?? null;

  useEffect(() => {
    if (!isExternal) {
      return;
    }
    let cancelled = false;
    void getService(service.id)
      .then((detail) => {
        if (!cancelled) setAdoptDiagnostics(detail.runtime?.adopt_diagnostics);
      })
      .catch((error: unknown) => {
        if (!cancelled) setDiagnosticsError(describeError(error));
      });
    return () => {
      cancelled = true;
    };
  }, [isExternal, service.id]);

  const enabledActions = useMemo(
    () => service.actions.filter((a) => a.enabled),
    [service.actions],
  );
  const findAction = (type: ActionType) =>
    enabledActions.find((a) => a.type === type);

  const handleRun = async (action: ActionMeta) => {
    const response = await run(service.id, action.id, {
      onSuccess: () => onMutated(),
    });
    if (response?.ok) {
      toast.push(`${action.label}: 성공`, "success");
    }
  };

  const handleAction = (action: ActionMeta) => {
    if (action.type === "show_logs") {
      onOpenLogs();
      return;
    }
    if (action.type === "open_url") {
      openServiceUrl(service.open_url);
      return;
    }
    if (shouldConfirmAction(action)) {
      setConfirm(action);
      return;
    }
    void handleRun(action);
  };

  const stopAction = findAction("process_stop");
  const stopVisibility = service.lifecycle.stop_visibility;
  const showStopAsPrimary = stopAction && stopVisibility === "primary";

  const restartAction = findAction("process_restart");
  const restartVisibility = service.lifecycle.restart_visibility;
  const showRestartAsPrimary = restartAction && restartVisibility === "primary";

  // primary 영역에 보일 액션 정렬
  const primaryActions: ActionMeta[] = [];
  for (const type of PRIMARY_ACTION_ORDER) {
    const action = findAction(type);
    if (action) primaryActions.push(action);
  }

  const logsAction = findAction("show_logs");

  return (
    <>
      <div className={classes.detailsPanel}>
        {isExternal && (
          <div className={classes.externalNotice}>
            <div>
              외부에서 이미 같은 포트로 실행 중인 인스턴스가 응답하고 있습니다.
              컨트롤 서버에서 직접 시작/중지하려면 외부 인스턴스를 먼저 종료하세요.
            </div>
            <Button
              size="sm"
              variant="danger"
              onClick={() => setKillOpen(true)}
              disabled={killing}
              loading={killing}
            >
              외부 인스턴스 종료
            </Button>
          </div>
        )}
        {isExternal && (
          <div className={classes.detailsGrid}>
            <Detail label="ADOPTION DIAGNOSTICS">
              {diagnosticsError ? (
                <span className={classes.detailValueMuted}>진단 조회 실패: {diagnosticsError}</span>
              ) : adoptDiagnostics ? (
                <span className={classes.detailValueMuted}>
                  {adoptDiagnostics.reason}
                  {adoptDiagnostics.candidate_pid ? ` · candidate pid ${adoptDiagnostics.candidate_pid}` : ""}
                  {adoptDiagnostics.health_ok !== null ? ` · health=${adoptDiagnostics.health_ok ? "ok" : "failed"}` : ""}
                  {adoptDiagnostics.expected_command ? ` · expected: ${formatCommand(adoptDiagnostics.expected_command)}` : ""}
                  {adoptDiagnostics.adopt_command ? ` · adopt: ${formatCommand(adoptDiagnostics.adopt_command)}` : ""}
                  {adoptDiagnostics.actual_cmdline ? ` · actual: ${formatCommand(adoptDiagnostics.actual_cmdline)}` : ""}
                  {adoptDiagnostics.cwd_expected ? ` · cwd expected: ${adoptDiagnostics.cwd_expected}` : ""}
                  {adoptDiagnostics.cwd_actual ? ` · cwd actual: ${adoptDiagnostics.cwd_actual}` : ""}
                </span>
              ) : (
                <span className={classes.detailValueMuted}>진단 확인 중…</span>
              )}
            </Detail>
          </div>
        )}
        <div className={classes.detailsActions}>
          {primaryActions.map((action) => {
            const isStart = action.type === "process_start";
            const isPrimary = isStart;
            const disabled =
              (isStart && (isExternal || !!service.runtime?.alive)) ||
              (!!pendingActionId && pendingActionId !== action.id);
            return (
              <Button
                key={action.id}
                variant={isPrimary ? "primary" : "default"}
                size="sm"
                loading={pendingActionId === action.id}
                disabled={disabled}
                onClick={() => handleAction(action)}
              >
                {action.label}
              </Button>
            );
          })}
          {showRestartAsPrimary && restartAction && (
            <Button
              key={restartAction.id}
              variant="primary"
              size="sm"
              loading={pendingActionId === restartAction.id}
              disabled={
                !service.runtime?.alive ||
                isExternal ||
                (!!pendingActionId && pendingActionId !== restartAction.id)
              }
              onClick={() => handleAction(restartAction)}
            >
              {restartAction.label}
            </Button>
          )}
          {logsAction && (
            <Button
              size="sm"
              onClick={() => handleAction(logsAction)}
              disabled={!!pendingActionId}
            >
              {logsAction.label}
            </Button>
          )}
          {showStopAsPrimary && stopAction && (
            <Button
              variant="danger"
              size="sm"
              loading={pendingActionId === stopAction.id}
              disabled={
                isExternal ||
                (!!pendingActionId && pendingActionId !== stopAction.id)
              }
              onClick={() => handleAction(stopAction)}
            >
              {stopAction.label}
            </Button>
          )}
        </div>

        <div className={classes.detailsGrid}>
          <Detail label="STATUS">
            <span className={classes.detailValue}>
              <StatusDot state={service.runtime?.state ?? "unknown"} />
            </span>
            {service.runtime?.last_exit_time && (
              <span className={classes.detailValueMuted}>
                {" · "}exit {formatExitCode(service.runtime.last_exit_code)} ·{" "}
                {formatRelative(service.runtime.last_exit_time)}
              </span>
            )}
          </Detail>
          <Detail label="PORT / URL">
            <span className={classes.detailValue}>
              {formatPort(service.port)}
              {service.open_url ? (
                <span className={classes.detailValueMuted}>
                  {" · "}
                  {service.open_url.replace(/^https?:\/\//, "")}
                </span>
              ) : null}
            </span>
          </Detail>
          <Detail label="RUNTIME">
            <span className={classes.detailValue}>
              {service.runtime?.alive ? formatUptime(service.runtime.uptime_seconds) : "—"}
              <span className={classes.detailValueMuted}>
                {" · "}pid {formatPid(service.runtime?.pid)}
              </span>
            </span>
            <ResourceLine service={service} />
          </Detail>
          <Detail label="ENTRY">
            <span className={classes.detailValue}>
              {service.cwd}/{service.entry_file}
            </span>
          </Detail>
          <Detail label="COMMAND">
            <span className={classes.detailValue}>{formatCommand(service.command)}</span>
          </Detail>
          <Detail label="HEALTH">
            <HealthLine service={service} />
          </Detail>
          <Detail label="ENV">
            <span className={`${classes.detailValue} ${classes.detailValueMuted}`}>
              {formatEnv(service.env)}
            </span>
          </Detail>
          <Detail label="LIFECYCLE">
            <span className={classes.detailValueMuted}>
              {service.lifecycle.mode} · stop={service.lifecycle.stop_visibility} ·
              autostart={service.lifecycle.autostart ? "on" : "off"}
            </span>
          </Detail>
          {service.runtime?.last_exit_time && (
            <Detail label="LAST EXIT">
              <span className={classes.detailValueMuted}>
                code {formatExitCode(service.runtime.last_exit_code)} ·{" "}
                {formatTime(service.runtime.last_exit_time)}
              </span>
            </Detail>
          )}
        </div>
      </div>

      {confirm && (
        <ConfirmDialog
          open
          title={getActionConfirmCopy(service.name, confirm).title}
          message={getActionConfirmCopy(service.name, confirm).message}
          confirmLabel={getActionConfirmCopy(service.name, confirm).confirmLabel}
          variant="danger"
          loading={pendingActionId === confirm.id}
          onCancel={() => setConfirm(undefined)}
          onConfirm={async () => {
            await handleRun(confirm);
            setConfirm(undefined);
          }}
        />
      )}

      <ExternalKillDialog
        open={killOpen}
        serviceName={service.name}
        pid={externalPid}
        loading={killing}
        onCancel={() => setKillOpen(false)}
        onConfirm={async () => {
          setKilling(true);
          try {
            const res = await killExternal(service.id);
            toast.push(
              `외부 인스턴스 종료 (pid ${res.killed.pid}, ${res.killed.signal})`,
              "success",
            );
            onMutated();
            setKillOpen(false);
          } catch (err) {
            toast.push(`종료 실패: ${describeError(err)}`, "error");
          } finally {
            setKilling(false);
          }
        }}
      />
    </>
  );
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className={classes.detailLabel}>{label}</div>
      <div>{children}</div>
    </div>
  );
}

function ResourceLine({ service }: { service: ServiceMeta }) {
  const resource = service.runtime?.resource;
  if (!resource?.available) {
    return (
      <span className={classes.detailValueMuted}>
        CPU — · RAM —{resource?.reason ? ` · ${resource.reason}` : ""}
      </span>
    );
  }
  const partialLabel = resource.partial
    ? resource.skipped_process_count > 0
      ? ` · 일부 누락 ${resource.skipped_process_count}`
      : " · 일부 누락"
    : "";
  return (
    <span className={classes.detailValueMuted} title={RESOURCE_USAGE_HELP}>
      CPU {formatCpuPercent(resource.cpu_percent)} · RAM{" "}
      {formatMemory(resource.memory_rss_bytes)} (RSS 합계) · proc {resource.process_count}
      {resource.children_count > 0 ? ` (${resource.children_count} child)` : ""}
      {partialLabel}
    </span>
  );
}

function HealthLine({ service }: { service: ServiceMeta }) {
  const health = service.runtime?.health;
  if (!service.health.enabled) {
    return <span className={classes.detailValueMuted}>비활성</span>;
  }
  // health 결과를 먼저 본다. running_external은 alive=false라도 ok=true이다.
  if (health?.ok === true) {
    return (
      <span className={`${classes.detailValue} ${classes.detailHealthOk}`}>
        OK · {health.elapsed_seconds ?? 0}s · {health.url}
      </span>
    );
  }
  if (health?.ok === false && service.runtime?.alive) {
    return (
      <span className={`${classes.detailValue} ${classes.detailHealthBad}`}>
        FAIL · {health.reason ?? `status ${health.status_code ?? "?"}`} · {health.url}
      </span>
    );
  }
  if (!service.runtime?.alive) {
    return <span className={classes.detailValueMuted}>n/a</span>;
  }
  return <span className={classes.detailValueMuted}>대기 중…</span>;
}
