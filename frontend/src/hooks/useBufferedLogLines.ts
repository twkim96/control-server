import { useCallback, useEffect, useRef, useState } from "react";

const FLUSH_DELAY_MS = 100;

export interface LogLineEntry {
  id: number;
  text: string;
}

export interface BufferedLogLinesState {
  lines: LogLineEntry[];
  appendLine: (line: string) => void;
  clear: () => void;
}

export function useBufferedLogLines(maxLines: number): BufferedLogLinesState {
  const [lines, setLines] = useState<LogLineEntry[]>([]);
  const linesRef = useRef<LogLineEntry[]>([]);
  const pendingRef = useRef<LogLineEntry[]>([]);
  const flushHandleRef = useRef<number | undefined>(undefined);
  const nextIdRef = useRef(0);

  const flush = useCallback(() => {
    flushHandleRef.current = undefined;
    if (pendingRef.current.length === 0) return;

    const next = [...linesRef.current, ...pendingRef.current];
    pendingRef.current = [];
    if (next.length > maxLines) {
      next.splice(0, next.length - maxLines);
    }
    linesRef.current = next;
    setLines(next);
  }, [maxLines]);

  const scheduleFlush = useCallback(() => {
    if (flushHandleRef.current !== undefined) return;
    flushHandleRef.current = window.setTimeout(flush, FLUSH_DELAY_MS);
  }, [flush]);

  const appendLine = useCallback(
    (line: string) => {
      pendingRef.current.push({ id: nextIdRef.current, text: line });
      nextIdRef.current += 1;
      scheduleFlush();
    },
    [scheduleFlush],
  );

  const clear = useCallback(() => {
    if (flushHandleRef.current !== undefined) {
      window.clearTimeout(flushHandleRef.current);
      flushHandleRef.current = undefined;
    }
    pendingRef.current = [];
    linesRef.current = [];
    nextIdRef.current = 0;
    setLines([]);
  }, []);

  useEffect(() => {
    const current = linesRef.current;
    if (current.length <= maxLines) return;

    const next = current.slice(current.length - maxLines);
    linesRef.current = next;
    setLines(next);
  }, [maxLines]);

  useEffect(() => {
    return () => {
      if (flushHandleRef.current !== undefined) {
        window.clearTimeout(flushHandleRef.current);
      }
      flushHandleRef.current = undefined;
      pendingRef.current = [];
    };
  }, []);

  return { lines, appendLine, clear };
}
