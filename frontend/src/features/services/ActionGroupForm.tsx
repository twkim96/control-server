import { useEffect, useState } from "react";
import type { DragEvent, ReactNode } from "react";

import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { ReorderControls } from "../../components/ReorderControls";
import { useToast } from "../../components/useToast";
import { createActionGroup, updateActionGroup } from "../../api/actions";
import { describeError } from "../../utils/errors";
import { PathPicker } from "../service-form/PathPicker";
import { PythonPicker } from "./PythonPicker";
import classes from "./services.module.css";
import componentClasses from "../../components/components.module.css";
import {
  getVerticalDropPosition,
  moveByIndex,
  type DropPosition,
} from "../../utils/reorder";
import type {
  ActionGroup,
  ActionItem,
  ActionKind,
  ExternalLogMeta,
} from "../../types/action";

export interface ActionGroupFormProps {
  open: boolean;
  initial: ActionGroup | null;  // null이면 신규 등록
  onClose: () => void;
  onSaved: () => void;
}

interface ItemDraft {
  id: string;
  name: string;
  description: string;
  kind: ActionKind;
  cwd: string;
  command: string;  // 공백/줄바꿈 split
  envText: string;
  keepRuns: string;
  externalLogs: ExternalLogMeta[];
}

const DEFAULT_ITEM: ItemDraft = {
  id: "",
  name: "",
  description: "",
  kind: "argv",
  cwd: "",
  command: "",
  envText: "",
  keepRuns: "20",
  externalLogs: [],
};

export function ActionGroupForm({
  open,
  initial,
  onClose,
  onSaved,
}: ActionGroupFormProps) {
  const [groupId, setGroupId] = useState("");
  const [groupName, setGroupName] = useState("");
  const [groupDesc, setGroupDesc] = useState("");
  const [items, setItems] = useState<ItemDraft[]>([{ ...DEFAULT_ITEM }]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [draggingItem, setDraggingItem] = useState<{
    index: number;
    overIndex?: number;
    position?: DropPosition;
  } | null>(null);
  const toast = useToast();

  useEffect(() => {
    if (!open) return;
    if (initial) {
      // 모달이 열릴 때 초기값 주입. open=true 직후에만 실행되어 cascading render는 한 번뿐.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setGroupId(initial.id);
      setGroupName(initial.name);
      setGroupDesc(initial.description);
      setItems(initial.items.map(itemToDraft));
    } else {
      setGroupId("");
      setGroupName("");
      setGroupDesc("");
      setItems([{ ...DEFAULT_ITEM }]);
    }
    setError(undefined);
  }, [open, initial]);

  if (!open) return null;

  const isEdit = initial !== null;

  const handleSubmit = async () => {
    setError(undefined);
    if (!groupId || !groupName) {
      setError("그룹 id와 name이 필요합니다.");
      return;
    }
    if (items.length === 0) {
      setError("최소 1개의 명령이 필요합니다.");
      return;
    }
    let payload: ActionGroup;
    try {
      payload = {
        id: groupId.trim(),
        name: groupName.trim(),
        description: groupDesc.trim(),
        items: items.map(draftToItem),
      };
    } catch (err) {
      setError(describeError(err));
      return;
    }

    setSubmitting(true);
    try {
      if (isEdit) {
        await updateActionGroup(initial.id, payload);
      } else {
        await createActionGroup(payload);
      }
      toast.push("저장했습니다.", "default");
      onSaved();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setSubmitting(false);
    }
  };

  const handleItemChange = (index: number, patch: Partial<ItemDraft>) => {
    setItems((prev) => prev.map((it, i) => (i === index ? { ...it, ...patch } : it)));
  };

  const handleAddItem = () => {
    setItems((prev) => [...prev, { ...DEFAULT_ITEM }]);
  };

  const handleRemoveItem = (index: number) => {
    setItems((prev) => prev.filter((_, i) => i !== index));
  };

  const handleMoveItem = (index: number, delta: 1 | -1) => {
    const target = index + delta;
    if (target < 0 || target >= items.length) return;
    setItems((prev) => {
      const next = [...prev];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };

  const handleDropItem = (
    sourceIndex: number,
    targetIndex: number,
    position: DropPosition,
  ) => {
    setItems((prev) => moveByIndex(prev, sourceIndex, targetIndex, position));
    setDraggingItem(null);
  };

  return (
    <div
      className={componentClasses.modalOverlay}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={componentClasses.modalCard}
        role="dialog"
        aria-modal="true"
        style={{ maxWidth: "720px", width: "92vw" }}
      >
        <div className={componentClasses.modalHeader}>
          {isEdit ? "Action Group 편집" : "Action Group 등록"}
        </div>
        <div
          className={componentClasses.modalBody}
          style={{ maxHeight: "70vh", overflow: "auto" }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: "var(--space-3)",
            }}
          >
            <Field label="Group ID" hint="영문/숫자/_/-">
              <Input
                value={groupId}
                onChange={(e) => setGroupId(e.target.value)}
                disabled={isEdit}
              />
            </Field>
            <Field label="Group Name">
              <Input
                value={groupName}
                onChange={(e) => setGroupName(e.target.value)}
              />
            </Field>
            <Field label="Description">
              <Input
                value={groupDesc}
                onChange={(e) => setGroupDesc(e.target.value)}
              />
            </Field>
          </div>

          <div style={{ marginTop: "var(--space-4)" }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "var(--space-2)",
              }}
            >
              <strong>명령 ({items.length})</strong>
              <Button size="sm" variant="ghost" onClick={handleAddItem}>
                + 명령 추가
              </Button>
            </div>
            <div className={classes.itemList}>
              {items.map((item, i) => (
                <ItemEditor
                  key={i}
                  index={i}
                  item={item}
                  totalCount={items.length}
                  onChange={(patch) => handleItemChange(i, patch)}
                  onRemove={() => handleRemoveItem(i)}
                  onMoveUp={() => handleMoveItem(i, -1)}
                  onMoveDown={() => handleMoveItem(i, 1)}
                  dragging={draggingItem?.index === i}
                  dropPosition={
                    draggingItem?.overIndex === i ? draggingItem.position : undefined
                  }
                  onDragStart={(e) => {
                    e.dataTransfer.setData("text/plain", String(i));
                    setDraggingItem({ index: i });
                  }}
                  onDragEnd={() => setDraggingItem(null)}
                  onDragOver={(e) => {
                    if (!draggingItem || draggingItem.index === i) return;
                    e.preventDefault();
                    e.dataTransfer.dropEffect = "move";
                    const position = getVerticalDropPosition(
                      e.clientY,
                      e.currentTarget.getBoundingClientRect(),
                    );
                    setDraggingItem({
                      ...draggingItem,
                      overIndex: i,
                      position,
                    });
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    if (!draggingItem || !draggingItem.position) return;
                    handleDropItem(draggingItem.index, i, draggingItem.position);
                  }}
                />
              ))}
            </div>
          </div>

          {error && (
            <div
              style={{
                marginTop: "var(--space-3)",
                color: "var(--status-error)",
                fontSize: "var(--font-sm)",
              }}
            >
              {error}
            </div>
          )}
        </div>
        <div className={componentClasses.modalFooter}>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            취소
          </Button>
          <Button variant="primary" onClick={handleSubmit} loading={submitting}>
            저장
          </Button>
        </div>
      </div>
    </div>
  );
}

interface ItemEditorProps {
  index: number;
  item: ItemDraft;
  totalCount: number;
  onChange: (patch: Partial<ItemDraft>) => void;
  onRemove: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  dragging?: boolean;
  dropPosition?: DropPosition;
  onDragStart?: (event: DragEvent<HTMLButtonElement>) => void;
  onDragEnd?: () => void;
  onDragOver?: (event: DragEvent<HTMLElement>) => void;
  onDrop?: (event: DragEvent<HTMLElement>) => void;
}

function ItemEditor({
  index,
  item,
  totalCount,
  onChange,
  onRemove,
  onMoveUp,
  onMoveDown,
  dragging = false,
  dropPosition,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
}: ItemEditorProps) {
  // PathPicker는 모달이라 한 번에 하나만 띄운다.
  // - "cwd": 디렉터리 선택
  // - external log index N: 파일 선택
  const [picker, setPicker] = useState<"cwd" | { logIndex: number } | null>(null);
  const [pythonPickerOpen, setPythonPickerOpen] = useState(false);

  const handleAddLog = () => {
    onChange({
      externalLogs: [...item.externalLogs, { name: "", path: "" }],
    });
  };
  const handleLogChange = (i: number, patch: Partial<ExternalLogMeta>) => {
    const next = item.externalLogs.map((log, j) =>
      j === i ? { ...log, ...patch } : log,
    );
    onChange({ externalLogs: next });
  };
  const handleRemoveLog = (i: number) => {
    onChange({
      externalLogs: item.externalLogs.filter((_, j) => j !== i),
    });
  };

  return (
    <div
      className={[
        classes.item,
        dragging ? classes.dragging : "",
        dropPosition === "before" ? classes.dropBefore : "",
        dropPosition === "after" ? classes.dropAfter : "",
      ]
        .filter(Boolean)
        .join(" ")}
      style={{ flexDirection: "column", alignItems: "stretch", gap: "var(--space-2)" }}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "var(--space-2)",
        }}
      >
        <div style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)" }}>
          <ReorderControls
            canMoveUp={index > 0}
            canMoveDown={index < totalCount - 1}
            onMoveUp={onMoveUp}
            onMoveDown={onMoveDown}
            dragging={dragging}
            onDragStart={onDragStart}
            onDragEnd={onDragEnd}
          />
          <strong>#{index + 1}</strong>
        </div>
        <Button size="sm" variant="ghost" onClick={onRemove}>
          삭제
        </Button>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
          gap: "var(--space-3)",
        }}
      >
        <Field label="ID" hint="영문/숫자/_/-">
          <Input value={item.id} onChange={(e) => onChange({ id: e.target.value })} />
        </Field>
        <Field label="Name">
          <Input
            value={item.name}
            onChange={(e) => onChange({ name: e.target.value })}
          />
        </Field>
        <Field label="Kind">
          <select
            value={item.kind}
            onChange={(e) => onChange({ kind: e.target.value as ActionKind })}
            style={{
              padding: "8px 10px",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--border-default)",
              background: "var(--bg-surface)",
              color: "var(--text-primary)",
            }}
          >
            <option value="argv">argv (시스템 명령)</option>
            <option value="python">python (cwd + 인터프리터)</option>
          </select>
        </Field>
        {item.kind === "python" && (
          <Field label="CWD" hint="allowed_path_roots 안의 디렉터리">
            <div style={{ display: "flex", gap: "var(--space-2)" }}>
              <Input
                value={item.cwd}
                onChange={(e) => onChange({ cwd: e.target.value })}
                style={{ flex: 1 }}
              />
              <Button size="sm" variant="ghost" onClick={() => setPicker("cwd")}>
                찾기
              </Button>
            </div>
          </Field>
        )}
        <Field
          label="Command (공백/개행 split)"
          hint="예: python -u task.py 또는 example-tool subcommand --option"
        >
          <div style={{ display: "flex", gap: "var(--space-2)" }}>
            <Input
              value={item.command}
              onChange={(e) => onChange({ command: e.target.value })}
              style={{ flex: 1 }}
            />
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setPythonPickerOpen(true)}
              title="시스템에 설치된 Python 인터프리터 중에서 선택"
            >
              Python 선택
            </Button>
          </div>
        </Field>
        <Field label="keep_runs" hint="ActionItem당 보관할 run .log 개수">
          <Input
            type="number"
            value={item.keepRuns}
            onChange={(e) => onChange({ keepRuns: e.target.value })}
            onWheel={(e) => (e.target as HTMLInputElement).blur()}
          />
        </Field>
        <Field label="ENV (KEY=VALUE 줄바꿈)" hint="비워두면 없음">
          <Input
            value={item.envText}
            onChange={(e) => onChange({ envText: e.target.value })}
          />
        </Field>
      </div>

      <div
        style={{
          marginTop: "var(--space-2)",
          paddingTop: "var(--space-2)",
          borderTop: "1px solid var(--border-subtle)",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-2)",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span
            style={{
              fontSize: "var(--font-sm)",
              color: "var(--text-secondary)",
            }}
          >
            외부 로그 파일 ({item.externalLogs.length})
          </span>
          <Button size="sm" variant="ghost" onClick={handleAddLog}>
            + 외부 로그
          </Button>
        </div>
        {item.externalLogs.length === 0 ? (
          <span style={{ fontSize: "var(--font-xs)", color: "var(--text-muted)" }}>
            예: success.log / fail.log. 카드의 chip으로 떠서 클릭 시 tail로 볼 수 있습니다.
          </span>
        ) : (
          item.externalLogs.map((log, i) => (
            <div
              key={i}
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(110px, 0.4fr) minmax(180px, 1fr) auto auto",
                gap: "var(--space-2)",
                alignItems: "center",
              }}
            >
              <Input
                placeholder="이름 (예: success)"
                value={log.name}
                onChange={(e) => handleLogChange(i, { name: e.target.value })}
              />
              <Input
                placeholder="/Users/.../success.log"
                value={log.path}
                onChange={(e) => handleLogChange(i, { path: e.target.value })}
              />
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setPicker({ logIndex: i })}
              >
                찾기
              </Button>
              <Button size="sm" variant="ghost" onClick={() => handleRemoveLog(i)}>
                삭제
              </Button>
            </div>
          ))
        )}
      </div>

      {picker !== null && (
        <PathPicker
          mode={picker === "cwd" ? "dir" : "file"}
          initialPath={
            picker === "cwd"
              ? item.cwd || undefined
              : item.externalLogs[picker.logIndex]?.path || item.cwd || undefined
          }
          onCancel={() => setPicker(null)}
          onSelect={(path) => {
            if (picker === "cwd") {
              onChange({ cwd: path });
            } else {
              handleLogChange(picker.logIndex, { path });
            }
            setPicker(null);
          }}
        />
      )}

      <PythonPicker
        open={pythonPickerOpen}
        onClose={() => setPythonPickerOpen(false)}
        onSelect={(path) => {
          // command 첫 토큰만 교체. 나머지 인자는 유지.
          // 예: "python -u scanner.py" + "/opt/anaconda3/bin/python3"
          //   → "/opt/anaconda3/bin/python3 -u scanner.py"
          const tokens = item.command.trim().split(/\s+/).filter(Boolean);
          const rest = tokens.slice(1);
          const next = [path, ...rest].join(" ");
          onChange({ command: next });
          setPythonPickerOpen(false);
        }}
      />
    </div>
  );
}

function itemToDraft(item: ActionItem): ItemDraft {
  return {
    id: item.id,
    name: item.name,
    description: item.description,
    kind: item.kind,
    cwd: item.cwd ?? "",
    command: item.command.join(" "),
    envText: Object.entries(item.env)
      .map(([k, v]) => `${k}=${v}`)
      .join("\n"),
    keepRuns: String(item.log.keep_runs),
    externalLogs: [...item.external_logs],
  };
}

function draftToItem(draft: ItemDraft): ActionItem {
  if (!draft.id || !draft.name) {
    throw new Error("ID와 name은 필수입니다.");
  }
  // 단순 split: 따옴표 처리는 v1.2.0 범위 밖. 인자에 공백이 있으면 운영자가 알아서.
  const command = draft.command
    .split(/\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (command.length === 0) {
    throw new Error(`#${draft.name} command가 비어있습니다.`);
  }
  if (draft.kind === "python" && !draft.cwd.trim()) {
    throw new Error(`#${draft.name} python kind는 cwd가 필요합니다.`);
  }
  const env: Record<string, string> = {};
  for (const line of draft.envText.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const eq = trimmed.indexOf("=");
    if (eq < 0) {
      throw new Error(`ENV 형식이 잘못됨: ${trimmed} (KEY=VALUE)`);
    }
    env[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1);
  }
  const keepRuns = Number.parseInt(draft.keepRuns, 10);
  if (Number.isNaN(keepRuns) || keepRuns < 0) {
    throw new Error("keep_runs는 0 이상의 정수여야 합니다.");
  }
  // 외부 로그: 한 entry라도 이름/경로가 비면 거부.
  const externalLogs: ExternalLogMeta[] = [];
  for (const log of draft.externalLogs) {
    const name = log.name.trim();
    const path = log.path.trim();
    if (!name && !path) continue;  // 둘 다 비면 등록 안 한 것으로 간주
    if (!name || !path) {
      throw new Error(
        `#${draft.name} 외부 로그: 이름과 경로 둘 다 입력해야 합니다.`,
      );
    }
    externalLogs.push({ name, path });
  }
  return {
    id: draft.id.trim(),
    name: draft.name.trim(),
    description: draft.description.trim(),
    kind: draft.kind,
    cwd: draft.kind === "python" ? draft.cwd.trim() : draft.cwd.trim() || null,
    command,
    env,
    log: { enabled: true, keep_runs: keepRuns },
    external_logs: externalLogs,
  };
}


interface FieldProps {
  label: string;
  hint?: string;
  children: ReactNode;
}

function Field({ label, hint, children }: FieldProps) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
      <span style={{ fontSize: "var(--font-sm)", color: "var(--text-secondary)" }}>
        {label}
      </span>
      {children}
      {hint && (
        <span style={{ fontSize: "var(--font-xs)", color: "var(--text-muted)" }}>
          {hint}
        </span>
      )}
    </label>
  );
}
