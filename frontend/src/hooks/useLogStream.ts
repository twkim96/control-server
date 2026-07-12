import { useEffect, useState } from "react";

import { apiBaseUrl } from "../api/client";
import {
  useBufferedLogLines,
  type LogLineEntry,
} from "./useBufferedLogLines";

export interface LogStreamOptions {
  // 화면에 보관할 최대 줄 수. 초과분은 잘라낸다.
  maxLines?: number;
  // 자동 재연결 backoff (ms). 기본 1000.
  backoffMs?: number;
  // 처음 연결될 때까지 시도할지 여부.
  enabled?: boolean;
}

export interface LogStreamState {
  lines: LogLineEntry[];
  connected: boolean;
  error: string | undefined;
  // 화면에서 강제로 비울 때 사용.
  clear: () => void;
}

// SSE 기반 실시간 로그 스트림 훅.
// EventSource는 Authorization 헤더를 못 붙이지만, GET /logs/stream은
// Phase 1~3 동안 인증이 없고 Phase 4부터는 cookie 기반이라 그대로 동작한다.
export function useLogStream(
  serviceId: string | undefined,
  options: LogStreamOptions = {},
): LogStreamState {
  const { maxLines = 1000, backoffMs = 1000, enabled = true } = options;

  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);
  const {
    lines,
    appendLine,
    clear: clearLines,
  } = useBufferedLogLines(maxLines);

  useEffect(() => {
    if (!serviceId || !enabled) {
      return;
    }

    clearLines();
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setConnected(false);
    setError(undefined);

    let aborted = false;
    let source: EventSource | null = null;
    let retryHandle: number | undefined;

    const connect = () => {
      const base = apiBaseUrl();
      const url = `${base}/api/services/${encodeURIComponent(serviceId)}/logs/stream`;
      source = new EventSource(url, { withCredentials: true });

      source.addEventListener("open", () => {
        if (aborted) return;
        setConnected(true);
        setError(undefined);
      });

      source.addEventListener("line", (event) => {
        if (aborted) return;
        const ev = event as MessageEvent;
        try {
          const payload = JSON.parse(ev.data) as { data?: string };
          appendLine(payload.data ?? "");
        } catch {
          appendLine(ev.data);
        }
      });

      source.addEventListener("error", () => {
        if (aborted) return;
        setConnected(false);
        setError("disconnected");
        source?.close();
        retryHandle = window.setTimeout(connect, backoffMs);
      });
    };

    connect();

    return () => {
      aborted = true;
      if (retryHandle !== undefined) window.clearTimeout(retryHandle);
      source?.close();
    };
  }, [serviceId, backoffMs, enabled, appendLine, clearLines]);

  return {
    lines,
    connected,
    error,
    clear: clearLines,
  };
}
