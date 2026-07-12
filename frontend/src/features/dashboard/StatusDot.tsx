import type { ServiceStatePhase } from "../../types/service";
import classes from "./dashboard.module.css";

const LABEL: Record<ServiceStatePhase, string> = {
  running: "Running",
  running_external: "External",
  unhealthy: "Unhealthy",
  stopped: "Stopped",
  starting: "Starting",
  stopping: "Stopping",
  unknown: "Unknown",
};

export function StatusDot({ state }: { state: ServiceStatePhase }) {
  return (
    <span className={classes.statusGroup}>
      <span className={`${classes.statusDot} ${classes[`dot_${state}`]}`} aria-hidden />
      <span className={classes.statusLabel}>{LABEL[state]}</span>
    </span>
  );
}
