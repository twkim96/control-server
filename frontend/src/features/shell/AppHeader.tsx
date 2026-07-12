import { NavLink, Link } from "react-router-dom";

import { getControllerResource } from "../../api/system";
import { usePolling } from "../../hooks/usePolling";
import { formatCpuPercent, formatMemory } from "../../utils/format";
import classes from "./AppHeader.module.css";

interface TabDef {
  to: string;
  label: string;
  // 정확히 / 일치할 때만 active로 보고 싶으면 true.
  end?: boolean;
}

const TABS: TabDef[] = [
  { to: "/", label: "Main", end: true },
  { to: "/servers", label: "Servers" },
  { to: "/services", label: "Services" },
  { to: "/settings", label: "Settings" },
];

export function AppHeader() {
  const controllerResource = usePolling(getControllerResource, 10000, true);
  const resource = controllerResource.data?.resource;

  return (
    <header className={classes.shell}>
      <div className={classes.topBar}>
        <div className={classes.topInner}>
          <Link to="/" className={classes.brand} aria-label="메인으로">
            Server Control
          </Link>

          <div className={classes.right} aria-label="컨트롤 서버 자원 사용량">
            <span className={classes.metricLabel}>Control</span>
            <span className={classes.metric}>
              CPU {resource?.available ? formatCpuPercent(resource.cpu_percent) : "—"}
            </span>
            <span className={classes.metric}>
              RAM {resource?.available ? formatMemory(resource.memory_rss_bytes) : "—"}
            </span>
          </div>
        </div>
      </div>

      <div className={classes.navBar}>
        <nav className={classes.tabs} aria-label="주요 메뉴">
          {TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              end={tab.end}
              className={({ isActive }) =>
                `${classes.tab} ${isActive ? classes.tabActive : ""}`
              }
            >
              {tab.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </header>
  );
}
