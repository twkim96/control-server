import type { ReactNode } from "react";

import classes from "./components.module.css";

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className={classes.empty}>{children}</div>;
}
