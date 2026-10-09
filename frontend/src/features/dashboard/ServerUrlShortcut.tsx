import type { ReactNode } from "react";

import { openServiceUrl } from "../../utils/openServiceUrl";
import classes from "./dashboard.module.css";

export function ServerUrlShortcut({
  url,
  name,
  children,
}: {
  url: string | null | undefined;
  name: string;
  children: ReactNode;
}) {
  if (!url) return <>{children}</>;

  return (
    <button
      type="button"
      className={classes.urlShortcut}
      data-stop-row-toggle
      aria-label={`${name} 웹페이지 열기 (새 탭)`}
      title="웹페이지 열기 (새 탭)"
      onClick={(event) => {
        event.stopPropagation();
        openServiceUrl(url);
      }}
      onKeyDown={(event) => event.stopPropagation()}
    >
      {children}
    </button>
  );
}
