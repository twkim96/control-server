import { useEffect, useRef, useState, useCallback } from "react";

export interface PollingState<T> {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
  refresh: () => Promise<void>;
}

// 일정 간격으로 fetcher를 호출하며 결과를 보관한다.
// fetcher가 throw하면 error에 담고 polling은 계속 유지한다.
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  enabled = true,
): PollingState<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error>();
  const [loading, setLoading] = useState(false);
  const fetcherRef = useRef(fetcher);
  const inFlightRef = useRef<Promise<void> | undefined>(undefined);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    fetcherRef.current = fetcher;
  }, [fetcher]);

  const refresh = useCallback(() => {
    if (inFlightRef.current) return inFlightRef.current;

    if (mountedRef.current) setLoading(true);
    const task = (async () => {
      try {
        const next = await fetcherRef.current();
        if (!mountedRef.current) return;
        setData(next);
        setError(undefined);
      } catch (err) {
        if (!mountedRef.current) return;
        setError(err instanceof Error ? err : new Error(String(err)));
      } finally {
        if (mountedRef.current) setLoading(false);
      }
    })();
    inFlightRef.current = task;
    void task.finally(() => {
      if (inFlightRef.current === task) {
        inFlightRef.current = undefined;
      }
    });
    return task;
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timerId: number | undefined;
    let generation = 0;

    const isVisible = () =>
      typeof document === "undefined" || document.visibilityState === "visible";

    const stopTimer = () => {
      if (timerId !== undefined) {
        window.clearTimeout(timerId);
        timerId = undefined;
      }
    };

    const startCycle = () => {
      const currentGeneration = ++generation;
      stopTimer();
      if (cancelled || !isVisible()) return;

      void refresh().finally(() => {
        if (
          cancelled ||
          currentGeneration !== generation ||
          !isVisible()
        ) {
          return;
        }
        timerId = window.setTimeout(startCycle, intervalMs);
      });
    };

    const onVisibilityChange = () => {
      if (isVisible()) {
        startCycle();
      } else {
        generation += 1;
        stopTimer();
      }
    };

    startCycle();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      cancelled = true;
      generation += 1;
      stopTimer();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [intervalMs, enabled, refresh]);

  return { data, error, loading, refresh };
}
