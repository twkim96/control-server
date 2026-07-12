import { useEffect, useState } from "react";

import { tailLogs } from "../../api/services";
import { Button } from "../../components/Button";
import { IconButton } from "../../components/IconButton";
import { useLogStream } from "../../hooks/useLogStream";
import { LogTerminal } from "./LogTerminal";
import classes from "./logs.module.css";

export interface LogDrawerProps {
  open: boolean;
  serviceId: string | undefined;
  serviceName: string | undefined;
  serviceAlive?: boolean;
  onClose: () => void;
}

export function LogDrawer(props: LogDrawerProps) {
  // 닫혔을 때는 컴포넌트 자체를 마운트하지 않는다.
  // 이래야 누적된 lines 상태와 SSE 연결이 매번 깨끗하게 초기화된다.
  if (!props.open || !props.serviceId) return null;
  return <LogDrawerInner {...props} />;
}

function LogDrawerInner({
  serviceId,
  serviceName,
  serviceAlive,
  onClose,
}: LogDrawerProps) {
  const shouldStream = serviceAlive === true;
  const stream = useLogStream(serviceId, { enabled: shouldStream, maxLines: 1500 });
  const [staticLog, setStaticLog] = useState<{
    serviceId: string;
    lines: string[];
    error?: string;
  } | null>(null);

  useEffect(() => {
    if (shouldStream || !serviceId) {
      return;
    }

    let cancelled = false;
    void tailLogs(serviceId, { tail: 500 })
      .then((res) => {
        if (cancelled) return;
        setStaticLog({ serviceId, lines: res.lines });
      })
      .catch((err) => {
        if (cancelled) return;
        setStaticLog({
          serviceId,
          lines: [],
          error: err instanceof Error ? err.message : "log load failed",
        });
      });

    return () => {
      cancelled = true;
    };
  }, [serviceId, shouldStream]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const status: "streaming" | "reconnecting" | "stopped" = !shouldStream
    ? "stopped"
    : stream.connected
      ? "streaming"
      : "reconnecting";
  const matchingStaticLog =
    staticLog && staticLog.serviceId === serviceId ? staticLog : null;
  const staticLines =
    matchingStaticLog && !matchingStaticLog.error ? matchingStaticLog.lines : [];
  const staticError = matchingStaticLog?.error;

  const dotClass =
    status === "streaming"
      ? `${classes.connectionDot} ${classes.connected}`
      : status === "stopped"
        ? `${classes.connectionDot} ${classes.disconnected}`
        : classes.connectionDot;

  return (
    <div
      className={classes.drawerOverlay}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className={classes.drawerCard} role="dialog" aria-modal="true">
        <div className={classes.drawerHeader}>
          <div>
            <div className={classes.drawerTitle}>{serviceName ?? serviceId} 로그</div>
            <div className={classes.drawerSub}>{serviceId}</div>
          </div>
          <div className={classes.drawerActions}>
            <span className={classes.connectionPill}>
              <span className={dotClass} />
              {status === "streaming"
                ? "streaming"
                : status === "reconnecting"
                  ? "reconnecting…"
                  : staticError
                    ? "log load failed"
                    : "static log"}
            </span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                stream.clear();
                if (serviceId) setStaticLog({ serviceId, lines: [] });
              }}
            >
              clear
            </Button>
            <IconButton label="닫기" onClick={onClose}>
              <CloseIcon />
            </IconButton>
          </div>
        </div>
        <div className={classes.drawerBody}>
          <LogTerminal lines={shouldStream ? stream.lines : staticLines} />
        </div>
      </div>
    </div>
  );
}

function CloseIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M3 3 L11 11 M11 3 L3 11" strokeLinecap="round" />
    </svg>
  );
}
