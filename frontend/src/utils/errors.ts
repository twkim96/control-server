import { ApiRequestError } from "../api/client";

export function describeError(err: unknown): string {
  if (err instanceof ApiRequestError) {
    return err.message;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return String(err);
}
