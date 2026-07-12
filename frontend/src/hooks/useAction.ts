import { useCallback, useState } from "react";

import { triggerAction } from "../api/services";
import type { ActionResponse } from "../types/service";
import { describeError } from "../utils/errors";

export interface UseActionState {
  pendingActionId: string | undefined;
  error: string | undefined;
  run: (
    serviceId: string,
    actionId: string,
    options?: { onSuccess?: (response: ActionResponse) => void },
  ) => Promise<ActionResponse | undefined>;
}

export function useAction(): UseActionState {
  const [pendingActionId, setPending] = useState<string | undefined>();
  const [error, setError] = useState<string | undefined>();

  const run = useCallback(
    async (
      serviceId: string,
      actionId: string,
      options?: { onSuccess?: (response: ActionResponse) => void },
    ) => {
      setPending(actionId);
      setError(undefined);
      try {
        const response = await triggerAction(serviceId, actionId);
        options?.onSuccess?.(response);
        return response;
      } catch (err) {
        setError(describeError(err));
        return undefined;
      } finally {
        setPending(undefined);
      }
    },
    [],
  );

  return { pendingActionId, error, run };
}
