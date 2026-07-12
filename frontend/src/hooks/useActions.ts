import { useCallback } from "react";

import { listActions } from "../api/actions";
import { usePolling } from "./usePolling";

const ACTIONS_POLL_INTERVAL_MS = 3000;

export function useActions(intervalMs = ACTIONS_POLL_INTERVAL_MS) {
  const fetcher = useCallback(() => listActions(), []);
  return usePolling(fetcher, intervalMs);
}
