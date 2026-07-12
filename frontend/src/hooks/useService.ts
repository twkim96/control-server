import { useCallback } from "react";

import { getService } from "../api/services";
import { usePolling } from "./usePolling";

const SERVICE_POLL_INTERVAL_MS = 2000;

export function useService(id: string | undefined, intervalMs = SERVICE_POLL_INTERVAL_MS) {
  const fetcher = useCallback(() => {
    if (!id) return Promise.reject(new Error("service id required"));
    return getService(id);
  }, [id]);
  return usePolling(fetcher, intervalMs, Boolean(id));
}
