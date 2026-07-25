import { useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent } from "react";

import { useToast } from "../../components/useToast";
import { reorderServices } from "../../api/config";
import { useServices } from "../../hooks/useServices";
import { LogDrawer } from "../logs/LogDrawer";
import type { ServiceMeta, ServiceStatePhase } from "../../types/service";
import { describeError } from "../../utils/errors";
import {
  getVerticalDropPosition,
  moveById,
  orderById,
  type DropPosition,
} from "../../utils/reorder";
import { DashboardToolbar, type StatusFilter } from "./DashboardToolbar";
import { ServerTable } from "./ServerTable";

const ISSUE_STATES: ServiceStatePhase[] = ["unhealthy", "unknown"];

export interface ServersSectionProps {
  // 검색/필터 도구를 표시할지. Main에서는 끄고, Servers 탭에서는 켠다.
  showToolbar?: boolean;
}

export function ServersSection({ showToolbar = true }: ServersSectionProps) {
  const services = useServices();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [logTarget, setLogTarget] = useState<ServiceMeta | undefined>();
  const [dragging, setDragging] = useState<{
    id: string;
    overId?: string;
    position?: DropPosition;
  } | null>(null);
  const [optimisticOrder, setOptimisticOrder] = useState<string[] | undefined>();
  const toast = useToast();

  const rawList = useMemo(
    () => services.data?.services ?? [],
    [services.data],
  );

  // 서버가 보낸 id 집합이 바뀌면(추가/삭제) 낙관적 순서는 더 이상 유효하지 않다.
  // 같은 집합이면 유지해 폴링마다 화면이 서버 순서로 튀는 깜빡임을 막는다.
  const rawIdKey = rawList.map((s) => s.id).slice().sort().join("\u0000");
  const prevIdKeyRef = useRef(rawIdKey);
  useEffect(() => {
    if (prevIdKeyRef.current !== rawIdKey) {
      prevIdKeyRef.current = rawIdKey;
      setOptimisticOrder(undefined);
    }
  }, [rawIdKey]);

  const list = useMemo(
    () => orderById(rawList, optimisticOrder),
    [rawList, optimisticOrder],
  );
  const filtered = useMemo(() => filterServices(list, search, filter), [list, search, filter]);
  const total = list.length;
  const running = useMemo(
    () =>
      list.filter(
        (s) =>
          s.runtime?.state === "running" || s.runtime?.state === "running_external",
      ).length,
    [list],
  );

  const handleReorder = async (
    sourceId: string,
    targetId: string,
    position: DropPosition,
  ) => {
    const currentOrder = list.map((s) => s.id);
    const next = moveById(currentOrder, sourceId, targetId, position);
    setDragging(null);
    if (next === currentOrder) return;
    setOptimisticOrder(next);
    try {
      await reorderServices(next);
    } catch (err) {
      setOptimisticOrder(undefined);
      void services.refresh();
      toast.push(
        err instanceof Error ? err.message : "순서 변경 실패",
        "error",
      );
    }
  };

  return (
    <>
      {showToolbar && (
        <DashboardToolbar
          total={total}
          running={running}
          search={search}
          onSearchChange={setSearch}
          filter={filter}
          onFilterChange={setFilter}
          onRefresh={() => services.refresh()}
          refreshing={services.loading}
        />
      )}

      {services.error && (
        <div role="alert" style={{ color: "var(--danger)", fontSize: 14 }}>
          {describeError(services.error)}
        </div>
      )}

      {services.data?.supervisor?.degraded && (
        <div
          role="status"
          style={{
            color: "var(--status-starting)",
            fontSize: 14,
            padding: "8px 0",
          }}
        >
          PM2 상태 조회가 지연되어 마지막 정상 상태를 표시 중입니다
          {services.data.supervisor.snapshot_age_seconds !== null
            ? ` (${Math.round(services.data.supervisor.snapshot_age_seconds)}초 전)`
            : ""}
          . 시작·중지·재시작은 PM2의 확정 응답이 있을 때만 실행됩니다.
        </div>
      )}

      <ServerTable
        services={filtered}
        onMutated={() => services.refresh()}
        onOpenLogs={(service) => setLogTarget(service)}
        orderedIds={list.map((s) => s.id)}
        dragging={dragging}
        onDragStart={(serviceId, e: DragEvent<HTMLButtonElement>) => {
          e.dataTransfer.setData("text/plain", serviceId);
          setDragging({ id: serviceId });
        }}
        onDragEnd={() => setDragging(null)}
        onDragOver={(serviceId, e: DragEvent<HTMLElement>) => {
          if (!dragging || dragging.id === serviceId) return;
          e.preventDefault();
          e.dataTransfer.dropEffect = "move";
          const position = getVerticalDropPosition(
            e.clientY,
            e.currentTarget.getBoundingClientRect(),
          );
          setDragging({ ...dragging, overId: serviceId, position });
        }}
        onDrop={(serviceId, e: DragEvent<HTMLElement>) => {
          e.preventDefault();
          if (!dragging?.id || !dragging.position) return;
          void handleReorder(dragging.id, serviceId, dragging.position);
        }}
        onMove={async (sid, delta) => {
          const all = list.map((s) => s.id);
          const idx = all.indexOf(sid);
          const target = idx + delta;
          if (idx < 0 || target < 0 || target >= all.length) return;
          const next = [...all];
          [next[idx], next[target]] = [next[target], next[idx]];
          try {
            await reorderServices(next);
            void services.refresh();
          } catch (err) {
            toast.push(
              err instanceof Error ? err.message : "순서 변경 실패",
              "error",
            );
          }
        }}
      />

      <LogDrawer
        open={!!logTarget}
        serviceId={logTarget?.id}
        serviceName={logTarget?.name}
        serviceAlive={
          logTarget
            ? list.find((s) => s.id === logTarget.id)?.runtime?.alive
            : undefined
        }
        onClose={() => setLogTarget(undefined)}
      />
    </>
  );
}

function filterServices(
  services: ServiceMeta[],
  search: string,
  filter: StatusFilter,
): ServiceMeta[] {
  const q = search.trim().toLowerCase();
  return services.filter((s) => {
    if (q) {
      const hay = `${s.name} ${s.id} ${s.description}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (filter === "running")
      return (
        s.runtime?.state === "running" || s.runtime?.state === "running_external"
      );
    if (filter === "stopped") return s.runtime?.state === "stopped";
    if (filter === "issues") return s.runtime ? ISSUE_STATES.includes(s.runtime.state) : true;
    return true;
  });
}
