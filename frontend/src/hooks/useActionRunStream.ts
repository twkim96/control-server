import { useEffect, useRef, useState } from "react";

import { apiBaseUrl } from "../api/client";
import {
  useBufferedLogLines,
  type LogLineEntry,
} from "./useBufferedLogLines";

export interface ActionRunStreamOptions {
  maxLines?: number;
  backoffMs?: number;
  enabled?: boolean;
}

export interface ActionRunStreamState {
  lines: LogLineEntry[];
  connected: boolean;
  ended: boolean;  // 백엔드가 'end' 이벤트를 보냈으면 true
  error: string | undefined;
  clear: () => void;
}

// /api/actions/runs/<run_id>/stream SSE 구독.
// run이 끝나면 서버가 'end' 이벤트를 보내고 connection이 닫힌다.
// 끝난 run을 다시 보고 싶으면 다른 API(getRun + tail)을 써야 한다.
export function useActionRunStream(
  runId: string | undefined,
  options: ActionRunStreamOptions = {},
): ActionRunStreamState {
  const { maxLines = 1500, backoffMs = 1000, enabled = true } = options;

  const [connected, setConnected] = useState(false);
  const [ended, setEnded] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);
  const endedRef = useRef(false);
  const {
    lines,
    appendLine,
    clear: clearLines,
  } = useBufferedLogLines(maxLines);

  useEffect(() => {
    if (!runId || !enabled) return;

    // runId가 바뀔 때마다 모든 상태를 명시적으로 리셋. 모달이 열린 채로 다른 run을
    // 선택했을 때 이전 run의 줄/error/ended가 새 run에 섞이는 사고를 방지한다.
    clearLines();
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setConnected(false);
    setEnded(false);
    setError(undefined);
    endedRef.current = false;

    let aborted = false;
    let source: EventSource | null = null;
    let retryHandle: number | undefined;

    const connect = () => {
      const base = apiBaseUrl();
      const url = `${base}/api/actions/runs/${encodeURIComponent(runId)}/stream`;
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

      source.addEventListener("end", () => {
        if (aborted) return;
        endedRef.current = true;
        setEnded(true);
        setConnected(false);
        source?.close();
      });

      source.addEventListener("error", () => {
        if (aborted) return;
        setConnected(false);
        // ended 이후의 error는 정상 종료. 재연결하지 않는다.
        if (endedRef.current) {
          source?.close();
          return;
        }
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
  }, [runId, backoffMs, enabled, appendLine, clearLines]);

  return {
    lines,
    connected,
    ended,
    error,
    clear: clearLines,
  };
}
