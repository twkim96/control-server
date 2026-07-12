import { useCallback } from "react";

import { listServices } from "../api/services";
import { usePolling } from "./usePolling";

const SERVICES_POLL_INTERVAL_MS = 3000;

export function useServices(intervalMs = SERVICES_POLL_INTERVAL_MS) {
  const fetcher = useCallback(() => listServices(), []);
  return usePolling(fetcher, intervalMs);
}
