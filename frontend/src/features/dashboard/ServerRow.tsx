import { useMemo, useState } from "react";
import type { DragEvent } from "react";
import { useNavigate } from "react-router-dom";

import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Dropdown, type DropdownItem } from "../../components/Dropdown";
import { IconButton } from "../../components/IconButton";
import { useToast } from "../../components/useToast";
import { useAction } from "../../hooks/useAction";
import type { ActionMeta, ServiceMeta } from "../../types/service";
import {
  formatCpuPercent,
  formatMemory,
  formatPort,
  formatPid,
  RESOURCE_USAGE_HELP,
} from "../../utils/format";
import { openServiceUrl } from "../../utils/openServiceUrl";
import { formatRelative, formatUptime } from "../../utils/time";
import { ServerRowDetails } from "./ServerRowDetails";
import { StatusDot } from "./StatusDot";
import { getActionConfirmCopy, shouldConfirmAction } from "./actionConfirm";
import classes from "./dashboard.module.css";
import { ReorderControls } from "../../components/ReorderControls";
import type { DropPosition } from "../../utils/reorder";

export interface ServerRowProps {
  service: ServiceMeta;
  expanded: boolean;
  onToggle: () => void;
  onMutated: () => void;
  onOpenLogs: () => void;
  canMoveUp: boolean;
  canMoveDown: boolean;
  onMoveUp: () => void;
  onMoveDown: () => void;
  dragging?: boolean;
  dropPosition?: DropPosition;
  onDragStart?: (event: DragEvent<HTMLButtonElement>) => void;
  onDragEnd?: () => void;
  onDragOver?: (event: DragEvent<HTMLElement>) => void;
  onDrop?: (event: DragEvent<HTMLElement>) => void;
}

export function ServerRow({
  service,
  expanded,
  onToggle,
  onMutated,
  onOpenLogs,
  canMoveUp,
  canMoveDown,
  onMoveUp,
  onMoveDown,
  dragging = false,
  dropPosition,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
}: ServerRowProps) {
  const runtime = service.runtime;
  const state = runtime?.state ?? "unknown";

  const navigate = useNavigate();
  const { pendingActionId, run } = useAction();
  const toast = useToast();
  const [confirmAction, setConfirmAction] = useState<ActionMeta | undefined>();

  const moreItems = useMemo<DropdownItem[]>(() => {
    const enabled = service.actions.filter((a) => a.enabled);
    const items: DropdownItem[] = [];
    const isExternal = state === "running_external";

    const restartAction = enabled.find((a) => a.type === "process_restart");
    if (restartAction && service.lifecycle.restart_visibility === "danger_menu") {
      items.push({
        id: restartAction.id,
        label: restartAction.label,
        disabled: !runtime?.alive || isExternal || !!pendingActionId,
        onSelect: () => setConfirmAction(restartAction),
      });
    }

    const stopAction = enabled.find((a) => a.type === "process_stop");
    if (stopAction && service.lifecycle.stop_visibility !== "primary") {
      items.push({
        id: stopAction.id,
        label: stopAction.label,
        variant: "danger",
        disabled: !runtime?.alive || isExternal || !!pendingActionId,
        onSelect: () => {
          if (shouldConfirmAction(stopAction)) {
            setConfirmAction(stopAction);
          } else {
            void runAction(stopAction);
          }
        },
      });
    }

    if (service.open_url) {
      items.push({
        id: "open_url",
        label: "URL 열기",
        onSelect: () => {
          openServiceUrl(service.open_url);
        },
      });
    }

    items.push({
      id: "edit",
      label: "등록 정보 수정",
      onSelect: () => navigate(`/services/${encodeURIComponent(service.id)}/edit`),
    });

    return items;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [service, runtime, pendingActionId, state, canMoveUp, canMoveDown]);

  async function runAction(action: ActionMeta) {
    const response = await run(service.id, action.id, {
      onSuccess: () => onMutated(),
    });
    if (response?.ok) toast.push(`${action.label}: 성공`, "success");
  }

  return (
    <>
      <div
        className={[
          classes.tableRow,
          expanded ? classes.expanded : "",
          dragging ? classes.dragging : "",
          dropPosition === "before" ? classes.dropBefore : "",
          dropPosition === "after" ? classes.dropAfter : "",
        ]
          .filter(Boolean)
          .join(" ")}
        role="button"
        tabIndex={0}
        onDragOver={onDragOver}
        onDrop={onDrop}
        onClick={(e) => {
          // 더보기 버튼/드롭다운 영역 클릭은 토글 막기
          if ((e.target as HTMLElement).closest("[data-stop-row-toggle]")) return;
          onToggle();
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
      >
        <div className={classes.cellService}>
          <ReorderControls
            canMoveUp={canMoveUp}
            canMoveDown={canMoveDown}
            onMoveUp={onMoveUp}
            onMoveDown={onMoveDown}
            stopPropagation
            dragging={dragging}
            onDragStart={onDragStart}
            onDragEnd={onDragEnd}
          />
          <div className={classes.cellServiceText}>
            <span className={classes.cellServiceName}>{service.name}</span>
            <span className={classes.cellServiceMeta}>
              {service.id}
              {service.description ? ` · ${service.description}` : ""}
            </span>
          </div>
        </div>

        <div className={classes.cellPort} data-mobile-label="PORT / URL">
          <span className={classes.cellPortLine}>{formatPort(service.port)}</span>
          <div className={classes.cellPortMeta}>
            {service.open_url ? service.open_url.replace(/^https?:\/\//, "") : "—"}
          </div>
        </div>

        <div className={classes.cellRuntime} data-mobile-label="RUNTIME">
          <span className={classes.cellRuntimeMain}>
            {runtime?.alive ? formatUptime(runtime.uptime_seconds) : "—"}
          </span>
          <span
            className={classes.cellRuntimeSub}
            title={runtime?.resource?.available ? RESOURCE_USAGE_HELP : undefined}
          >
            {runtime?.resource?.available
              ? `CPU ${formatCpuPercent(runtime.resource.cpu_percent)} · RAM ${formatMemory(runtime.resource.memory_rss_bytes)}`
              : `pid ${formatPid(runtime?.pid)}`}
          </span>
        </div>

        <div className={classes.cellStatus} data-mobile-label="STATUS">
          <StatusDot state={state} />
          <span className={classes.cellStatusSub}>
            {runtime?.last_exit_time
              ? `exit ${runtime.last_exit_code ?? "—"} · ${formatRelative(runtime.last_exit_time)}`
              : runtime?.alive
                ? "live"
                : "—"}
          </span>
        </div>

        <div className={classes.cellStatusMobile} aria-hidden>
          <span className={`${classes.statusDot} ${classes[`dot_${state}`]}`} />
        </div>

        <div className={classes.cellTrailing} data-stop-row-toggle>
          {moreItems.length > 0 && (
            <Dropdown
              groups={[{ items: moreItems }]}
              trigger={
                <IconButton label="더보기">
                  <DotsIcon />
                </IconButton>
              }
            />
          )}
        </div>
      </div>

      {expanded && (
        <ServerRowDetails
          service={service}
          onMutated={onMutated}
          onOpenLogs={onOpenLogs}
        />
      )}

      {confirmAction && (
        <ConfirmDialog
          open
          title={getActionConfirmCopy(service.name, confirmAction).title}
          message={getActionConfirmCopy(service.name, confirmAction).message}
          confirmLabel={getActionConfirmCopy(service.name, confirmAction).confirmLabel}
          variant="danger"
          loading={pendingActionId === confirmAction.id}
          onCancel={() => setConfirmAction(undefined)}
          onConfirm={async () => {
            await runAction(confirmAction);
            setConfirmAction(undefined);
          }}
        />
      )}
    </>
  );
}

function DotsIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="currentColor"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      <circle cx="3" cy="7" r="1.4" />
      <circle cx="7" cy="7" r="1.4" />
      <circle cx="11" cy="7" r="1.4" />
    </svg>
  );
}
