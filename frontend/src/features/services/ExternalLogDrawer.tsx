import { useEffect, useRef, useState } from "react";

import { Button } from "../../components/Button";
import { IconButton } from "../../components/IconButton";
import { tailExternalLog } from "../../api/actions";
import { useToast } from "../../components/useToast";
import { LogTerminal } from "../logs/LogTerminal";
import logsClasses from "../logs/logs.module.css";
import type { ExternalLogMeta } from "../../types/action";

export interface ExternalLogDrawerProps {
  open: boolean;
  groupId: string | undefined;
  itemId: string | undefined;
  itemName: string | undefined;
  log: ExternalLogMeta | undefined;
  onClose: () => void;
}

const POLL_INTERVAL_MS = 2000;

export function ExternalLogDrawer(props: ExternalLogDrawerProps) {
  if (!props.open || !props.groupId || !props.itemId || !props.log) return null;
  return <ExternalLogDrawerInner {...props} />;
}

function ExternalLogDrawerInner({
  groupId,
  itemId,
  itemName,
  log,
  onClose,
}: ExternalLogDrawerProps) {
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState<string | undefined>();
  const stoppedRef = useRef(false);
  const toast = useToast();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // 짧은 폴링 기반 tail. 외부 로그는 대부분 작은 success.log/fail.log라
  // SSE 없이도 충분. 디스크 폴링 부담을 줄이려고 2초 간격.
  useEffect(() => {
    if (!groupId || !itemId || !log) return;
    stoppedRef.current = false;
    let cancelled = false;
    const poll = async () => {
      if (stoppedRef.current) return;
      try {
        const res = await tailExternalLog(groupId, itemId, log.path, { tail: 500 });
        if (cancelled) return;
        setLines(res.lines);
        setError(undefined);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "로그를 불러오지 못했습니다.");
      }
    };
    void poll();
    const id = window.setInterval(() => {
      void poll();
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      stoppedRef.current = true;
      window.clearInterval(id);
    };
  }, [groupId, itemId, log]);

  const handleCopyPath = () => {
    if (!log) return;
    void navigator.clipboard.writeText(log.path).then(
      () => toast.push("경로를 복사했습니다.", "default"),
      () => toast.push("복사 실패", "error"),
    );
  };

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
              {itemName ?? itemId} · {log?.name}
            </div>
            <div
              className={logsClasses.drawerSub}
              title={log?.path}
              style={{ maxWidth: "50vw", overflow: "hidden", textOverflow: "ellipsis" }}
            >
              {log?.path}
            </div>
          </div>
          <div className={logsClasses.drawerActions}>
            <Button size="sm" variant="ghost" onClick={handleCopyPath}>
              경로 복사
            </Button>
            <IconButton label="닫기" onClick={onClose}>
              <CloseIcon />
            </IconButton>
          </div>
        </div>
        <div className={logsClasses.drawerBody}>
          {error ? (
            <div style={{ color: "var(--status-error)", padding: "var(--space-3)" }}>
              {error}
            </div>
          ) : (
            <LogTerminal lines={lines} />
          )}
        </div>
      </div>
    </div>
  );
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
