import type { DragEvent, KeyboardEvent } from "react";

import { IconButton } from "./IconButton";
import classes from "./components.module.css";

export interface ReorderControlsProps {
  canMoveUp: boolean;
  canMoveDown: boolean;
  onMoveUp?: () => void;
  onMoveDown?: () => void;
  size?: "sm" | "md";
  // 부모가 클릭 처리(예: row 토글)을 갖고 있을 때 클릭 이벤트 버블링 방지.
  stopPropagation?: boolean;
  dragging?: boolean;
  onDragStart?: (event: DragEvent<HTMLButtonElement>) => void;
  onDragEnd?: (event: DragEvent<HTMLButtonElement>) => void;
}

// 항목 순서를 바꾸는 drag handle. 키보드 사용자는 핸들에 focus 후 ↑/↓로 이동한다.
export function ReorderControls({
  canMoveUp,
  canMoveDown,
  onMoveUp,
  onMoveDown,
  size = "sm",
  stopPropagation = false,
  dragging = false,
  onDragStart,
  onDragEnd,
}: ReorderControlsProps) {
  const canMove = canMoveUp || canMoveDown;
  const handleKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (stopPropagation) e.stopPropagation();
    if (e.key === "ArrowUp" && canMoveUp && onMoveUp) {
      e.preventDefault();
      onMoveUp();
    }
    if (e.key === "ArrowDown" && canMoveDown && onMoveDown) {
      e.preventDefault();
      onMoveDown();
    }
  };
  const cls = [classes.reorderControls, size === "md" ? classes.reorderControlsMd : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <div className={cls}>
      <IconButton
        label="드래그해서 순서 변경"
        className={dragging ? classes.reorderDragging : undefined}
        draggable={canMove}
        disabled={!canMove}
        onClick={(e) => {
          if (stopPropagation) e.stopPropagation();
        }}
        onMouseDown={(e) => {
          if (stopPropagation) e.stopPropagation();
        }}
        onKeyDown={handleKeyDown}
        onDragStart={(e) => {
          if (!canMove) return;
          if (stopPropagation) e.stopPropagation();
          e.dataTransfer.effectAllowed = "move";
          onDragStart?.(e);
        }}
        onDragEnd={(e) => {
          if (stopPropagation) e.stopPropagation();
          onDragEnd?.(e);
        }}
      >
        <GripIcon />
      </IconButton>
    </div>
  );
}

function GripIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 3.5h.01M9 3.5h.01M5 7h.01M9 7h.01M5 10.5h.01M9 10.5h.01" />
    </svg>
  );
}
