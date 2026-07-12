import { useState } from "react";
import type { DragEvent } from "react";

import { EmptyState } from "../../components/EmptyState";
import type { ServiceMeta } from "../../types/service";
import { ServerRow } from "./ServerRow";
import classes from "./dashboard.module.css";
import type { DropPosition } from "../../utils/reorder";

export interface ServerTableProps {
  services: ServiceMeta[];
  onMutated: () => void;
  onOpenLogs: (service: ServiceMeta) => void;
  onMove?: (serviceId: string, delta: 1 | -1) => void;
  // 정렬 가능한 전체 서비스 ID 리스트. compact 모드에서는 슬라이스된 view라
  // ↑↓ 활성화 판단을 위해 별도로 받는다. 비워두면 services 자체로 판단.
  orderedIds?: string[];
  dragging?: { id: string; overId?: string; position?: DropPosition } | null;
  onDragStart?: (serviceId: string, event: DragEvent<HTMLButtonElement>) => void;
  onDragEnd?: () => void;
  onDragOver?: (serviceId: string, event: DragEvent<HTMLElement>) => void;
  onDrop?: (serviceId: string, event: DragEvent<HTMLElement>) => void;
}

export function ServerTable({
  services,
  onMutated,
  onOpenLogs,
  onMove,
  orderedIds,
  dragging,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
}: ServerTableProps) {
  const [expandedId, setExpandedId] = useState<string | undefined>();
  const order = orderedIds ?? services.map((s) => s.id);

  return (
    <div className={classes.tableCard}>
      <div className={classes.tableHeader}>
        <div>SERVICE</div>
        <div>PORT / URL</div>
        <div>RUNTIME</div>
        <div>STATUS</div>
        <div />
      </div>
      {services.length === 0 ? (
        <EmptyState>등록된 서비스가 없습니다.</EmptyState>
      ) : (
        services.map((service) => {
          const idx = order.indexOf(service.id);
          return (
            <ServerRow
              key={service.id}
              service={service}
              expanded={expandedId === service.id}
              onToggle={() =>
                setExpandedId((cur) => (cur === service.id ? undefined : service.id))
              }
              onMutated={onMutated}
              onOpenLogs={() => onOpenLogs(service)}
              canMoveUp={!!onMove && idx > 0}
              canMoveDown={!!onMove && idx >= 0 && idx < order.length - 1}
              onMoveUp={() => onMove?.(service.id, -1)}
              onMoveDown={() => onMove?.(service.id, 1)}
              dragging={dragging?.id === service.id}
              dropPosition={
                dragging?.overId === service.id ? dragging.position : undefined
              }
              onDragStart={(e) => onDragStart?.(service.id, e)}
              onDragEnd={onDragEnd}
              onDragOver={(e) => onDragOver?.(service.id, e)}
              onDrop={(e) => onDrop?.(service.id, e)}
            />
          );
        })
      )}
    </div>
  );
}
