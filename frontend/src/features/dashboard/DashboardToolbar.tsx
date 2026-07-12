import { Button } from "../../components/Button";
import { Dropdown, type DropdownItem } from "../../components/Dropdown";
import { IconButton } from "../../components/IconButton";
import { Input } from "../../components/Input";
import classes from "./dashboard.module.css";

export type StatusFilter = "all" | "running" | "stopped" | "issues";

const FILTER_LABEL: Record<StatusFilter, string> = {
  all: "All",
  running: "Running",
  stopped: "Stopped",
  issues: "Issues",
};

export interface DashboardToolbarProps {
  total: number;
  running: number;
  search: string;
  onSearchChange: (value: string) => void;
  filter: StatusFilter;
  onFilterChange: (value: StatusFilter) => void;
  onRefresh: () => void;
  refreshing?: boolean;
}

export function DashboardToolbar({
  total,
  running,
  search,
  onSearchChange,
  filter,
  onFilterChange,
  onRefresh,
  refreshing,
}: DashboardToolbarProps) {
  const filterItems: DropdownItem[] = (Object.keys(FILTER_LABEL) as StatusFilter[]).map(
    (key) => ({
      id: key,
      label: FILTER_LABEL[key],
      onSelect: () => onFilterChange(key),
    }),
  );

  return (
    <div className={classes.toolbar}>
      <span className={classes.countPill}>
        {total} servers
        <span className={classes.countRunning}>· {running} running</span>
      </span>

      <div className={classes.searchBox}>
        <Input
          placeholder="이름 또는 ID 검색"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          iconLeft={<SearchIcon />}
        />
      </div>

      <Dropdown
        align="right"
        groups={[{ items: filterItems }]}
        trigger={
          <Button size="sm" iconRight={<ChevronIcon />}>
            Filter: {FILTER_LABEL[filter]}
          </Button>
        }
      />

      <IconButton label="새로고침" onClick={onRefresh} disabled={refreshing}>
        <RefreshIcon />
      </IconButton>
    </div>
  );
}

function SearchIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6">
      <circle cx="6" cy="6" r="4" />
      <line x1="9.2" y1="9.2" x2="12" y2="12" strokeLinecap="round" />
    </svg>
  );
}

function ChevronIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor">
      <path d="M2 4 L5 7 L8 4 Z" />
    </svg>
  );
}

function RefreshIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M11.5 6.5A4.5 4.5 0 1 1 7 2 V0.5 L9.5 2.5 L7 4.5 V3" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
