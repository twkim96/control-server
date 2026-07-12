import { useEffect, useRef, useState } from "react";

import { Button } from "../../components/Button";
import { IconButton } from "../../components/IconButton";
import { cancelRun, getRun } from "../../api/actions";
import { useActionRunStream } from "../../hooks/useActionRunStream";
import { useToast } from "../../components/useToast";
import { LogTerminal } from "../logs/LogTerminal";
import logsClasses from "../logs/logs.module.css";
import classes from "./services.module.css";
import type { ActionRun, ActionRunStatus } from "../../types/action";

const COMPLETED_RUN_TAIL_LINES = 500;

export interface ActionRunDrawerProps {
  open: boolean;
  runId: string | undefined;
  // 폴링 모드: live=true면 SSE 스트림. live=false면 종료된 run을 디스크에서 읽기.
  live: boolean;
  itemName?: string;
  onClose: () => void;
  onRunUpdated?: (run: ActionRun) => void;
}

export function ActionRunDrawer(props: ActionRunDrawerProps) {
  if (!props.open || !props.runId) return null;
  return <ActionRunDrawerInner {...props} />;
}

function ActionRunDrawerInner({
  runId,
  live,
  itemName,
  onClose,
  onRunUpdated,
}: ActionRunDrawerProps) {
  const stream = useActionRunStream(runId, { enabled: live, maxLines: 1500 });
  const [run, setRun] = useState<ActionRun | null>(null);
  const [staticLines, setStaticLines] = useState<string[]>([]);
  const [cancelling, setCancelling] = useState(false);
  const toast = useToast();
  const onRunUpdatedRef = useRef(onRunUpdated);

  useEffect(() => {
    onRunUpdatedRef.current = onRunUpdated;
  }, [onRunUpdated]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // run 메타데이터 폴링: SSE는 stdout 줄만 보낸다. status/exit_code는 별도 GET.
  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    let intervalId: number | undefined;
    const stopPolling = () => {
      if (intervalId !== undefined) {
        window.clearInterval(intervalId);
        intervalId = undefined;
      }
    };
    const pull = async () => {
      try {
        const res = await getRun(runId, {
          tail: live ? 0 : COMPLETED_RUN_TAIL_LINES,
        });
        if (cancelled) return;
        setRun(res.run);
        if (!live) setStaticLines(res.lines);
        if (live) {
          onRunUpdatedRef.current?.(res.run);
          if (res.run.status !== "running") stopPolling();
        }
      } catch {
        // 일시 실패는 무시. 다음 폴링에서 재시도.
      }
    };
    if (!live) {
      void pull();
      return () => {
        cancelled = true;
      };
    }
    intervalId = window.setInterval(() => {
      void pull();
    }, 1500);
    void pull();
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [runId, live]);

  const handleCancel = async () => {
    if (!runId) return;
    setCancelling(true);
    try {
      const res = await cancelRun(runId);
      setRun(res.run);
      onRunUpdated?.(res.run);
      toast.push("중지 요청을 보냈습니다.", "default");
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "중지 실패", "error");
    } finally {
      setCancelling(false);
    }
  };

  const lines = live ? stream.lines : staticLines;
  const status = (run?.status ?? "running") as ActionRunStatus;
  const isRunning = status === "running";

  return (
    <div
      className={logsClasses.drawerOverlay}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className={logsClasses.drawerCard} role="dialog" aria-modal="true">
        <div className={logsClasses.drawerHeader}>
          <div>
            <div className={logsClasses.drawerTitle}>
              {itemName ?? "Run"} 출력
            </div>
            <div className={logsClasses.drawerSub}>
              {runId} · {statusLabel(status, run?.exit_code)}
            </div>
          </div>
          <div className={logsClasses.drawerActions}>
            <span className={`${classes.statusPill} ${pillClass(status)}`}>
              {status}
            </span>
            {live && isRunning && (
              <Button
                size="sm"
                variant="danger"
                onClick={handleCancel}
                loading={cancelling}
              >
                중지
              </Button>
            )}
            <IconButton label="닫기" onClick={onClose}>
              <CloseIcon />
            </IconButton>
          </div>
        </div>
        <div className={logsClasses.drawerBody}>
          <LogTerminal lines={lines} />
        </div>
      </div>
    </div>
  );
}

function statusLabel(status: ActionRunStatus, exitCode: number | null | undefined): string {
  if (status === "running") return "실행 중";
  if (status === "succeeded") return "성공 (exit 0)";
  if (status === "failed") return `실패 (exit ${exitCode ?? "?"})`;
  if (status === "cancelled") return "사용자 중지";
  return status;
}

function pillClass(status: ActionRunStatus): string {
  switch (status) {
    case "running":
      return classes.statusRunning;
    case "succeeded":
      return classes.statusSucceeded;
    case "failed":
      return classes.statusFailed;
    case "cancelled":
      return classes.statusCancelled;
    default:
      return "";
  }
}

function CloseIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
    >
      <path d="M3 3 L11 11 M11 3 L3 11" strokeLinecap="round" />
    </svg>
  );
}
