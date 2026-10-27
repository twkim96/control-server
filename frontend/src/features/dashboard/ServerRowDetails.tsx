import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useToast } from "../../components/useToast";
import { useAction } from "../../hooks/useAction";
import { cancelRecovery, getService, getServiceEvents, killExternal } from "../../api/services";
import type { ActionMeta, ActionType, AdoptDiagnostics, ServiceEventsResponse, ServiceMeta } from "../../types/service";
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
import { ServerUrlShortcut } from "./ServerUrlShortcut";
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

const EVENT_LABELS: Record<string, string> = {
  start: "시작 요청",
  stop: "중지 요청",
  restart: "재시작 요청",
  online_observed: "PM2 온라인 상태 관측",
  stopped_observed: "PM2 중지 상태 관측",
  restart_observed: "재시작 감지 (원인 미상)",
  recovery_requested: "헬스 실패 복구 요청",
  recovery_succeeded: "헬스 실패 복구 완료",
  recovery_failed: "헬스 실패 복구 실패",
  daemon_restore_requested: "PM2 복구 시작 요청",
  daemon_restore_succeeded: "PM2 복구 완료",
  daemon_restore_blocked: "PM2 복구 차단",
  daemon_restore_failed: "PM2 복구 실패",
};

const RECOVERY_STATUS: Record<string, string> = {
  pending: "복구 대기", restoring: "복구 중", restored: "복구 완료",
  blocked: "복구 차단", failed: "복구 실패",
};
const RECOVERY_REASON: Record<string, string> = {
  definition_changed: "서비스 설정이 변경되었습니다.",
  existing_pm2_entry: "기존 PM2 항목의 상태를 확인하세요.",
  previous_process_unconfirmed: "이전 프로세스의 종료를 확인하지 못했습니다.",
  external_port_occupied: "외부 프로세스가 포트를 사용 중입니다.",
  dependency_not_ready: "선행 서비스가 준비되지 않았습니다.",
  dependency_missing: "선행 서비스를 찾을 수 없습니다.",
  pm2_start_failed: "PM2 시작에 실패했습니다.",
  readiness_failed: "서비스 준비 상태를 확인하지 못했습니다.",
  intent_state_unreadable: "자동 복구 설정을 확인하지 못했습니다.",
  intent_persistence_failed: "자동 복구 설정을 저장하지 못했습니다.",
  daemon_identity_unavailable: "관리 프로세스를 확인하지 못했습니다.",
  automatic_recovery_disabled: "자동 시작과 복구가 꺼져 있습니다.",
  automatic_start_budget_exhausted: "자동 시작 재시도 한도에 도달했습니다.",
};

export function ServerRowDetails({ service, onMutated, onOpenLogs }: ServerRowDetailsProps) {
  const { pendingActionId, run } = useAction();
  const toast = useToast();
  const [confirm, setConfirm] = useState<ActionMeta | undefined>();
  const [killOpen, setKillOpen] = useState(false);
  const [killing, setKilling] = useState(false);
  const [cancellingRecovery, setCancellingRecovery] = useState(false);
  const [events, setEvents] = useState<ServiceEventsResponse>();
  const [eventsError, setEventsError] = useState<string>();
  const [eventsLoading, setEventsLoading] = useState(false);

  const isExternal = !!service.runtime?.unmanaged;
  const canKillExternal = service.runtime?.state === "running_external";
  const externalPid = service.runtime?.unmanaged_pid ?? null;
  const recovery = service.runtime?.daemon_recovery;

  const refreshEvents = useCallback(async () => {
    setEventsLoading(true);
    setEventsError(undefined);
    try {
      setEvents(await getServiceEvents(service.id));
    } catch (err) {
      setEventsError(describeError(err));
    } finally {
      setEventsLoading(false);
    }
  }, [service.id]);

  useEffect(() => {
    let cancelled = false;
    void getServiceEvents(service.id)
      .then((response) => {
        if (!cancelled) setEvents(response);
      })
      .catch((err: unknown) => {
        if (!cancelled) setEventsError(describeError(err));
      });
    return () => {
      cancelled = true;
    };
  }, [service.id]);

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
              관리되지 않는 포트 점유 또는 health 응답이 감지되었습니다. 상태와 아래 진단을 확인하세요.
              {!canKillExternal && " 외부 종료는 identity와 health가 모두 확인된 경우에만 허용됩니다."}
            </div>
            <Button
              size="sm"
              variant="danger"
              onClick={() => setKillOpen(true)}
              disabled={killing || !canKillExternal}
              loading={killing}
            >
              외부 인스턴스 종료
            </Button>
          </div>
        )}
        {isExternal && (
          <div className={classes.detailsGrid}>
            <Detail label="EXTERNAL DIAGNOSTICS">
              <ExternalDiagnostics
                key={JSON.stringify([service.id, service.runtime?.state, externalPid, service.runtime?.health.ok, service.runtime?.adopt_diagnostics])}
                serviceId={service.id}
                provided={service.runtime?.adopt_diagnostics}
              />
            </Detail>
          </div>
        )}
        <div className={classes.detailsActions}>
          {recovery && (recovery.desired_state === "running" || isExternal) && (
            <>
              <Button
                size="sm"
                loading={cancellingRecovery}
                disabled={cancellingRecovery}
                onClick={async () => {
                  setCancellingRecovery(true);
                  try {
                    await cancelRecovery(service.id);
                    toast.push("자동 복구를 취소했습니다.", "success");
                    onMutated();
                  } catch (err) {
                    toast.push(`자동 복구 취소 실패: ${describeError(err)}`, "error");
                  } finally {
                    setCancellingRecovery(false);
                  }
                }}
              >
                자동 복구 취소
              </Button>
              <span className={classes.detailValueMuted}>실행 중인 프로세스는 종료하지 않습니다.</span>
            </>
          )}
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
              {isExternal && " · External (unmanaged)"}
            </span>
            {service.runtime?.last_exit_time && (
              <span className={classes.detailValueMuted}>
                {" · "}exit {formatExitCode(service.runtime.last_exit_code)} ·{" "}
                {formatRelative(service.runtime.last_exit_time)}
              </span>
            )}
          </Detail>
          {service.runtime?.daemon_recovery && (
            <Detail label="PM2 RECOVERY">
              <span className={classes.detailValueMuted}>
                목표: {service.runtime.daemon_recovery.desired_state === "running" ? "실행" : service.runtime.daemon_recovery.desired_state === "stopped" ? "중지" : "미확인"}
                {" · "}{RECOVERY_STATUS[service.runtime.daemon_recovery.status ?? ""] ?? "복구 상태 없음"}
                {service.runtime.daemon_recovery.reason &&
                  ` · ${RECOVERY_REASON[service.runtime.daemon_recovery.reason] ?? "복구 조건 확인 필요"}`}
              </span>
            </Detail>
          )}
          <Detail label="PORT / URL">
            <ServerUrlShortcut url={service.open_url} name={service.name}>
              <span className={classes.detailValue}>
                {formatPort(service.port)}
                {service.open_url ? (
                  <span className={classes.detailValueMuted}>
                    {" · "}
                    {service.open_url.replace(/^https?:\/\//, "")}
                  </span>
                ) : null}
              </span>
            </ServerUrlShortcut>
          </Detail>
          <Detail label="RUNTIME">
            <span className={classes.detailValue}>
              {service.runtime?.alive ? formatUptime(service.runtime.uptime_seconds) : "—"}
              <span className={classes.detailValueMuted}>
                {" · "}pid {formatPid(service.runtime?.pid)}
              </span>
            </span>
            {service.runtime?.supervision && (
              <span className={classes.detailValueMuted}>
                PM2 재시작 {service.runtime.supervision.restart_count}
                {service.runtime.supervision.pm2_status
                  ? ` · ${service.runtime.supervision.pm2_status}`
                  : ""}
              </span>
            )}
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
        <section className={classes.serviceHistory} aria-label="서비스 실행 이력">
          <header>
            <strong>
              실행 이력{
                service.runtime?.supervision || events
                  ? ` · PM2 재시작 ${service.runtime?.supervision?.restart_count ?? events?.restart_count ?? 0}회`
                  : ""
              }
            </strong>
            <Button size="sm" variant="ghost" onClick={() => void refreshEvents()} loading={eventsLoading}>
              새로고침
            </Button>
          </header>
          <div className={classes.detailValueMuted}>
            표시 시각은 상태를 관측한 시간이며 실제 종료 시각과 다를 수 있습니다.
          </div>
          {eventsError ? (
            <div className={classes.detailValueMuted} role="alert">이력을 불러오지 못했습니다: {eventsError}</div>
          ) : !events ? (
            <div className={classes.detailValueMuted}>이력 확인 중…</div>
          ) : events.events.length === 0 ? (
            <div className={classes.detailValueMuted}>기록된 실행 이력이 없습니다.</div>
          ) : (
            <ul>
              {events.events.slice().reverse().map((event) => (
                <li key={event.id}>
                  <time dateTime={new Date(event.at * 1000).toISOString()}>
                    관측 {formatTime(event.at)}
                  </time>
                  <span>{EVENT_LABELS[event.type] ?? event.message}</span>
                  {event.exit_code !== undefined && <span>exit {formatExitCode(event.exit_code)}</span>}
                  {event.restart_count !== undefined && <span>재시작 {event.restart_count}</span>}
                </li>
              ))}
            </ul>
          )}
        </section>
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

function ExternalDiagnostics({ serviceId, provided }: { serviceId: string; provided?: AdoptDiagnostics }) {
  const [diagnostics, setDiagnostics] = useState<AdoptDiagnostics>();
  const [error, setError] = useState<string>();
  const [refreshing, setRefreshing] = useState(false);
  useEffect(() => {
    if (provided) return;
    let cancelled = false;
    void getService(serviceId)
      .then((detail) => {
        if (!cancelled) setDiagnostics(detail.runtime?.adopt_diagnostics);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(describeError(err));
      });
    return () => { cancelled = true; };
  }, [serviceId, provided]);
  const current = diagnostics ?? provided;
  return (
    <span className={classes.detailValueMuted}>
      <Button size="sm" disabled={refreshing} loading={refreshing} onClick={async () => {
        setRefreshing(true);
        setError(undefined);
        try {
          const detail = await getService(serviceId);
          setDiagnostics(detail.runtime?.adopt_diagnostics);
        } catch (err) {
          setError(describeError(err));
        } finally {
          setRefreshing(false);
        }
      }}>진단 새로고침</Button>{" "}
      {error ? `진단 조회 실패: ${error}` : !current ? "진단 확인 중…" : <>
      {current.reason}
      {current.candidate_pid ? ` · candidate pid ${current.candidate_pid}` : ""}
      {current.health_ok !== null ? ` · health=${current.health_ok ? "ok" : "failed"}` : ""}
      {current.expected_command ? ` · expected: ${formatCommand(current.expected_command)}` : ""}
      {current.adopt_command ? ` · adopt: ${formatCommand(current.adopt_command)}` : ""}
      {current.actual_cmdline ? ` · actual: ${formatCommand(current.actual_cmdline)}` : ""}
      {current.cwd_expected ? ` · cwd expected: ${current.cwd_expected}` : ""}
      {current.cwd_actual ? ` · cwd actual: ${current.cwd_actual}` : ""}
      </>}
    </span>
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
    if (service.runtime?.unmanaged) {
      return <span className={classes.detailValueMuted}>외부 응답 OK · {health.url}</span>;
    }
    return (
      <span className={`${classes.detailValue} ${classes.detailHealthOk}`}>
        OK · {health.elapsed_seconds ?? 0}s · {health.url}
      </span>
    );
  }
  if (health?.ok === false && (service.runtime?.alive || service.runtime?.unmanaged)) {
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
