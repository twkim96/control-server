import { useState } from "react";
import type { DragEvent } from "react";

import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ReorderControls } from "../../components/ReorderControls";
import { Spinner } from "../../components/Spinner";
import { useToast } from "../../components/useToast";
import { cancelRun, deleteActionGroup, runAction } from "../../api/actions";
import { formatRelative } from "../../utils/time";
import classes from "./services.module.css";
import type { DropPosition } from "../../utils/reorder";
import type {
  ActionGroup,
  ActionItem,
  ActionRun,
  ActionRunStatus,
} from "../../types/action";

export interface ActionGroupCardProps {
  group: ActionGroup;
  onRunOpened: (runId: string, itemName: string, live: boolean) => void;
  onExternalLogOpened: (
    groupId: string,
    itemId: string,
    itemName: string,
    log: { name: string; path: string },
  ) => void;
  onMutated: () => void;
  onEdit: (group: ActionGroup) => void;
  // 순서 변경. canMoveUp/Down은 ServicesSection이 인덱스를 보고 채워서 넘긴다.
  canMoveUp: boolean;
  canMoveDown: boolean;
  onMoveUp: () => void;
  onMoveDown: () => void;
  dragging?: boolean;
  dropPosition?: DropPosition;
  onDragStart?: (event: DragEvent<HTMLButtonElement>) => void;
  onDragEnd?: (event: DragEvent<HTMLButtonElement>) => void;
  onDragOver?: (event: DragEvent<HTMLElement>) => void;
  onDrop?: (event: DragEvent<HTMLElement>) => void;
}

export function ActionGroupCard({
  group,
  onRunOpened,
  onExternalLogOpened,
  onMutated,
  onEdit,
  canMoveUp,
  canMoveDown,
  onMoveUp,
  onMoveDown,
  dragging = false,
  dropPosition,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
}: ActionGroupCardProps) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const toast = useToast();

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await deleteActionGroup(group.id);
      toast.push(`${group.name} 그룹을 삭제했습니다.`, "default");
      setConfirmDelete(false);
      onMutated();
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "삭제 실패", "error");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <article
      className={[
        classes.card,
        dragging ? classes.dragging : "",
        dropPosition === "before" ? classes.dropBefore : "",
        dropPosition === "after" ? classes.dropAfter : "",
      ]
        .filter(Boolean)
        .join(" ")}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      <header className={classes.cardHeader}>
        <div className={classes.cardTitleBlock}>
          <ReorderControls
            canMoveUp={canMoveUp}
            canMoveDown={canMoveDown}
            onMoveUp={onMoveUp}
            onMoveDown={onMoveDown}
            dragging={dragging}
            onDragStart={onDragStart}
            onDragEnd={onDragEnd}
          />
          <div className={classes.cardTitleText}>
            <div className={classes.cardTitle}>{group.name}</div>
            {group.description && (
              <div className={classes.cardDesc}>{group.description}</div>
            )}
          </div>
        </div>
        <div className={classes.cardActions}>
          <Button size="sm" variant="ghost" onClick={() => onEdit(group)}>
            편집
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setConfirmDelete(true)}
          >
            삭제
          </Button>
        </div>
      </header>

      <div className={classes.itemList}>
        {group.items.map((item) => (
          <ActionItemRow
            key={item.id}
            group={group}
            item={item}
            onRunOpened={onRunOpened}
            onExternalLogOpened={onExternalLogOpened}
            onMutated={onMutated}
          />
        ))}
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title={`${group.name} 그룹 삭제`}
        message="이 그룹과 모든 명령이 config에서 제거됩니다. 디스크에 저장된 .log 파일은 그대로 남습니다."
        confirmLabel="삭제"
        variant="danger"
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </article>
  );
}

interface ActionItemRowProps {
  group: ActionGroup;
  item: ActionItem;
  onRunOpened: (runId: string, itemName: string, live: boolean) => void;
  onExternalLogOpened: (
    groupId: string,
    itemId: string,
    itemName: string,
    log: { name: string; path: string },
  ) => void;
  onMutated: () => void;
}

function ActionItemRow({
  group,
  item,
  onRunOpened,
  onExternalLogOpened,
  onMutated,
}: ActionItemRowProps) {
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const toast = useToast();

  const recentRuns = item.recent_runs ?? [];
  const liveRun = recentRuns.find((r) => r.status === "running") ?? null;
  const lastFinished =
    recentRuns.find((r) => r.status !== "running") ?? null;

  const handleRun = async () => {
    setStarting(true);
    try {
      const res = await runAction(group.id, item.id);
      toast.push(`${item.name} 실행 시작`, "default");
      onRunOpened(res.run.run_id, item.name, true);
      onMutated();
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "실행 실패", "error");
    } finally {
      setStarting(false);
    }
  };

  const handleCancelLive = async () => {
    if (!liveRun) return;
    setCancelling(true);
    try {
      await cancelRun(liveRun.run_id);
      toast.push("중지 요청을 보냈습니다.", "default");
      onMutated();
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "중지 실패", "error");
    } finally {
      setCancelling(false);
    }
  };

  // 모바일에서 카드가 안 줄어들지 않도록 표시용 sub를 짧게 만든다.
  // 첫 토큰이 절대경로면 basename만 보여주고, 진짜 풀 경로는 title에서 호버로 본다.
  const subDisplay = (() => {
    const cmd = [...item.command];
    if (cmd[0] && cmd[0].includes("/")) {
      cmd[0] = cmd[0].slice(cmd[0].lastIndexOf("/") + 1);
    }
    return cmd.join(" ");
  })();
  const subFull = item.command.join(" ");

  return (
    <div className={classes.item}>
      <div className={classes.itemMeta}>
        <div className={classes.itemName}>{item.name}</div>
        <div className={classes.itemSub} title={subFull}>
          {item.kind === "python" ? "py" : "argv"} · {subDisplay}
        </div>
        {(liveRun || lastFinished) && (
          <RecentRunInline
            liveRun={liveRun}
            lastFinished={lastFinished}
            onOpenLive={() =>
              liveRun && onRunOpened(liveRun.run_id, item.name, true)
            }
            onOpenLast={() =>
              lastFinished &&
              onRunOpened(lastFinished.run_id, item.name, false)
            }
          />
        )}
        {item.external_logs.length > 0 && (
          <div className={classes.externalLogsBlock}>
            {item.external_logs.map((log) => (
              <button
                key={log.path}
                type="button"
                className={classes.externalLogChip}
                onClick={() =>
                  onExternalLogOpened(group.id, item.id, item.name, log)
                }
                title={log.path}
              >
                {log.name}
              </button>
            ))}
          </div>
        )}
      </div>
      <div className={classes.itemActions}>
        {liveRun ? (
          <>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => onRunOpened(liveRun.run_id, item.name, true)}
              iconLeft={<Spinner />}
            >
              로그
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={handleCancelLive}
              loading={cancelling}
            >
              중지
            </Button>
          </>
        ) : (
          <Button
            size="sm"
            variant="primary"
            onClick={handleRun}
            loading={starting}
          >
            실행
          </Button>
        )}
      </div>
    </div>
  );
}

interface RecentRunInlineProps {
  liveRun: ActionRun | null;
  lastFinished: ActionRun | null;
  onOpenLive: () => void;
  onOpenLast: () => void;
}

function RecentRunInline({
  liveRun,
  lastFinished,
  onOpenLive,
  onOpenLast,
}: RecentRunInlineProps) {
  if (liveRun) {
    return (
      <button
        type="button"
        className={classes.recentRow}
        onClick={onOpenLive}
        style={{ background: "none", border: 0, cursor: "pointer", padding: 0 }}
      >
        <span className={classes.recentRowMeta}>
          <span className={`${classes.statusPill} ${classes.statusRunning}`}>
            running
          </span>
          {formatRelative(liveRun.started_at)}
        </span>
      </button>
    );
  }
  if (!lastFinished) return null;
  return (
    <button
      type="button"
      className={classes.recentRow}
      onClick={onOpenLast}
      style={{ background: "none", border: 0, cursor: "pointer", padding: 0 }}
    >
      <span className={classes.recentRowMeta}>
        <span className={`${classes.statusPill} ${pillClass(lastFinished.status)}`}>
          {lastFinished.status}
        </span>
        {formatRelative(lastFinished.ended_at ?? lastFinished.started_at)}
        {lastFinished.exit_code !== null && (
          <span style={{ color: "var(--text-muted)" }}>
            exit {lastFinished.exit_code}
          </span>
        )}
      </span>
    </button>
  );
}

function pillClass(status: ActionRunStatus): string {
  switch (status) {
    case "running":
      return classes.statusRunning;
    case "succeeded":
      return classes.statusSucceeded;
    case "failed":
      return classes.statusFailed;
    case "cancelled":
      return classes.statusCancelled;
    default:
      return "";
  }
}
