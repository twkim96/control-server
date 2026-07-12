import { useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "../../components/Button";
import { EmptyState } from "../../components/EmptyState";
import { useToast } from "../../components/useToast";
import { reorderActionGroups } from "../../api/actions";
import { useActions } from "../../hooks/useActions";
import { ActionGroupCard } from "./ActionGroupCard";
import { ActionGroupForm } from "./ActionGroupForm";
import { ActionRunDrawer } from "./ActionRunDrawer";
import { ExternalLogDrawer } from "./ExternalLogDrawer";
import classes from "./services.module.css";
import mainClasses from "../main/main.module.css";
import {
  getVerticalDropPosition,
  moveById,
  orderById,
  type DropPosition,
} from "../../utils/reorder";
import type { ActionGroup, ExternalLogMeta } from "../../types/action";

interface OpenRunInfo {
  runId: string;
  itemName: string;
  live: boolean;
}

interface OpenExternalLogInfo {
  groupId: string;
  itemId: string;
  itemName: string;
  log: ExternalLogMeta;
}

export interface ServicesSectionProps {
  // compact=true: Main 페이지 안에 끼워 넣을 때. 헤더 우측에 "모두 보기" 버튼 추가,
  // 카드 그리드는 첫 N개만 보여준다.
  compact?: boolean;
  // 메인 페이지에서 보여줄 최대 그룹 수. compact일 때만 의미 있음.
  maxGroupsInCompact?: number;
}

export function ServicesSection({
  compact = false,
  maxGroupsInCompact = 4,
}: ServicesSectionProps) {
  const { data, error, refresh, loading } = useActions();
  const navigate = useNavigate();
  const [editing, setEditing] = useState<ActionGroup | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [openRun, setOpenRun] = useState<OpenRunInfo | null>(null);
  const [openExtLog, setOpenExtLog] = useState<OpenExternalLogInfo | null>(null);
  const [dragging, setDragging] = useState<{
    id: string;
    overId?: string;
    position?: DropPosition;
  } | null>(null);
  const [optimisticGroupOrder, setOptimisticGroupOrder] = useState<string[] | undefined>();
  const toast = useToast();

  const rawGroups = data?.groups ?? [];

  // 서버 id 집합이 바뀌면 낙관적 순서를 버린다. 같은 집합이면 유지해 깜빡임을 막는다.
  const rawIdKey = rawGroups.map((g) => g.id).slice().sort().join("\u0000");
  const prevIdKeyRef = useRef(rawIdKey);
  useEffect(() => {
    if (prevIdKeyRef.current !== rawIdKey) {
      prevIdKeyRef.current = rawIdKey;
      setOptimisticGroupOrder(undefined);
    }
  }, [rawIdKey]);

  const allGroups = orderById(rawGroups, optimisticGroupOrder);
  const groups = compact ? allGroups.slice(0, maxGroupsInCompact) : allGroups;
  const hiddenCount = compact ? Math.max(0, allGroups.length - groups.length) : 0;

  const handleAdd = () => {
    setEditing(null);
    setShowForm(true);
  };

  const handleEdit = (group: ActionGroup) => {
    setEditing(group);
    setShowForm(true);
  };

  const handleSaved = () => {
    setShowForm(false);
    setEditing(null);
    void refresh();
  };

  const handleRunOpened = (runId: string, itemName: string, live: boolean) => {
    setOpenRun({ runId, itemName, live });
  };

  const handleExternalLogOpened = (
    groupId: string,
    itemId: string,
    itemName: string,
    log: ExternalLogMeta,
  ) => {
    setOpenExtLog({ groupId, itemId, itemName, log });
  };

  const handleMove = async (index: number, delta: 1 | -1) => {
    const target = index + delta;
    if (target < 0 || target >= allGroups.length) return;
    const next = allGroups.map((g) => g.id);
    [next[index], next[target]] = [next[target], next[index]];
    try {
      await reorderActionGroups(next);
      void refresh();
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "순서 변경 실패", "error");
    }
  };

  const handleDrop = async (
    sourceId: string,
    targetId: string,
    position: DropPosition,
  ) => {
    const currentOrder = allGroups.map((g) => g.id);
    const next = moveById(currentOrder, sourceId, targetId, position);
    setDragging(null);
    if (next === currentOrder) return;
    setOptimisticGroupOrder(next);
    try {
      await reorderActionGroups(next);
    } catch (err) {
      setOptimisticGroupOrder(undefined);
      void refresh();
      toast.push(err instanceof Error ? err.message : "순서 변경 실패", "error");
    }
  };

  return (
    <>
      <header
        className={compact ? mainClasses.sectionHeader : classes.header}
      >
        <div>
          <h2 className={compact ? mainClasses.sectionTitle : classes.title}>
            Services
          </h2>
          <p className={compact ? mainClasses.sectionSub : classes.subtitle}>
            자주 쓰는 일회성 명령을 등록하고 버튼으로 실행
          </p>
        </div>
        <div
          className={compact ? mainClasses.sectionActions : classes.headerActions}
        >
          {compact && (
            <Button variant="ghost" onClick={() => navigate("/services")}>
              모두 보기
            </Button>
          )}
          <Button variant="primary" onClick={handleAdd}>
            그룹 추가
          </Button>
        </div>
      </header>

      {error && (
        <div
          style={{
            color: "var(--status-error)",
            fontSize: "var(--font-sm)",
          }}
        >
          액션 목록을 불러오지 못했습니다: {error.message}
        </div>
      )}

      {!data && loading && <div className={classes.empty}>로딩 중…</div>}

      {data && allGroups.length === 0 && (
        <EmptyState>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-3)",
              alignItems: "center",
            }}
          >
            <div>
              <strong style={{ display: "block", marginBottom: "var(--space-1)" }}>
                등록된 명령이 없습니다
              </strong>
              <span
                style={{ color: "var(--text-muted)", fontSize: "var(--font-sm)" }}
              >
                {'"그룹 추가" 버튼으로 첫 ActionGroup을 만들어보세요. py 스크립트와 alias 명령 모두 등록할 수 있습니다.'}
              </span>
            </div>
            <Button variant="primary" onClick={handleAdd}>
              그룹 추가
            </Button>
          </div>
        </EmptyState>
      )}

      {groups.length > 0 && (
        <div className={classes.groupGrid}>
          {groups.map((group) => {
            // compact 슬라이스 안에서도 전체 인덱스를 기준으로 순서 변경
            const fullIndex = allGroups.findIndex((g) => g.id === group.id);
            return (
              <ActionGroupCard
                key={group.id}
                group={group}
                onRunOpened={handleRunOpened}
                onExternalLogOpened={handleExternalLogOpened}
                onMutated={refresh}
                onEdit={handleEdit}
                canMoveUp={fullIndex > 0}
                canMoveDown={fullIndex < allGroups.length - 1}
                onMoveUp={() => void handleMove(fullIndex, -1)}
                onMoveDown={() => void handleMove(fullIndex, 1)}
                dragging={dragging?.id === group.id}
                dropPosition={
                  dragging?.overId === group.id ? dragging.position : undefined
                }
                onDragStart={(e) => {
                  e.dataTransfer.setData("text/plain", group.id);
                  setDragging({ id: group.id });
                }}
                onDragEnd={() => setDragging(null)}
                onDragOver={(e: DragEvent<HTMLElement>) => {
                  if (!dragging || dragging.id === group.id) return;
                  e.preventDefault();
                  e.dataTransfer.dropEffect = "move";
                  const position = getVerticalDropPosition(
                    e.clientY,
                    e.currentTarget.getBoundingClientRect(),
                  );
                  setDragging({ ...dragging, overId: group.id, position });
                }}
                onDrop={(e: DragEvent<HTMLElement>) => {
                  e.preventDefault();
                  if (!dragging?.id || !dragging.position) return;
                  void handleDrop(dragging.id, group.id, dragging.position);
                }}
              />
            );
          })}
        </div>
      )}

      {compact && hiddenCount > 0 && (
        <div
          style={{
            textAlign: "center",
            color: "var(--text-muted)",
            fontSize: "var(--font-sm)",
          }}
        >
          + {hiddenCount}개 더 (모두 보기에서 확인)
        </div>
      )}

      <ActionGroupForm
        open={showForm}
        initial={editing}
        onClose={() => setShowForm(false)}
        onSaved={handleSaved}
      />

      <ActionRunDrawer
        open={openRun !== null}
        runId={openRun?.runId}
        live={openRun?.live ?? false}
        itemName={openRun?.itemName}
        onClose={() => setOpenRun(null)}
        onRunUpdated={() => void refresh()}
      />

      <ExternalLogDrawer
        open={openExtLog !== null}
        groupId={openExtLog?.groupId}
        itemId={openExtLog?.itemId}
        itemName={openExtLog?.itemName}
        log={openExtLog?.log}
        onClose={() => setOpenExtLog(null)}
      />
    </>
  );
}
