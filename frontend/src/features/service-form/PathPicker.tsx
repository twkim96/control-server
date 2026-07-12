import { useEffect, useState } from "react";

import { Button } from "../../components/Button";
import { IconButton } from "../../components/IconButton";
import { listFiles } from "../../api/files";
import type { FileEntry } from "../../types/config";
import { describeError } from "../../utils/errors";
import classes from "./service-form.module.css";

export interface PathPickerProps {
  // 'dir'이면 디렉터리만 선택, 'file'이면 파일만 선택.
  mode: "dir" | "file";
  initialPath?: string;
  // 선택을 마쳤을 때.
  onSelect: (path: string) => void;
  onCancel: () => void;
}

export function PathPicker({ mode, initialPath, onSelect, onCancel }: PathPickerProps) {
  const [path, setPath] = useState<string | undefined>(initialPath);
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [roots, setRoots] = useState<FileEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | undefined>();

  useEffect(() => {
    let cancelled = false;
    // 비동기 fetch 시작을 위한 loading 표시. 직후 listFiles 콜백에서 다시 setState한다.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setError(undefined);
    listFiles(path)
      .then((res) => {
        if (cancelled) return;
        setEntries(res.entries);
        if (res.roots) setRoots(res.roots);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(describeError(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  const goUp = () => {
    if (!path) return;
    const idx = path.lastIndexOf("/");
    if (idx <= 0) {
      // 루트 목록으로 돌아감
      setPath(undefined);
      return;
    }
    setPath(path.slice(0, idx));
  };

  const onEntryClick = (entry: FileEntry) => {
    if (entry.is_dir) {
      setPath(entry.path);
      return;
    }
    if (mode === "file") {
      onSelect(entry.path);
    }
  };

  const showRoots = path === undefined;

  const canSelectCurrent =
    mode === "dir" && typeof path === "string" && path.length > 0;

  return (
    <div className={classes.pickerOverlay} onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div className={classes.pickerCard} role="dialog" aria-modal="true">
        <div className={classes.pickerHeader}>
          <span>{mode === "dir" ? "디렉터리 선택" : "파일 선택"}</span>
          <IconButton label="닫기" onClick={onCancel}>
            <CloseIcon />
          </IconButton>
        </div>
        <div className={classes.pickerCrumbs}>
          {showRoots ? "(허용 루트)" : path}
        </div>
        <div className={classes.pickerBody}>
          {error && <div className={classes.error} style={{ padding: 12 }}>{error}</div>}
          {loading && !error && <div style={{ padding: 12, color: "var(--text-muted)" }}>loading…</div>}

          {showRoots
            ? roots.map((root) => (
                <button
                  key={root.path}
                  type="button"
                  className={`${classes.pickerEntry} ${classes.dir}`}
                  onClick={() => setPath(root.path)}
                >
                  📁 {root.name}
                </button>
              ))
            : (
              <>
                <button
                  type="button"
                  className={`${classes.pickerEntry} ${classes.dir}`}
                  onClick={goUp}
                >
                  ..
                </button>
                {entries.map((entry) => {
                  const disabled = mode === "file" && entry.is_dir === false && false;
                  return (
                    <button
                      key={entry.path}
                      type="button"
                      className={`${classes.pickerEntry} ${entry.is_dir ? classes.dir : ""} ${disabled ? classes.disabled : ""}`}
                      onClick={() => onEntryClick(entry)}
                    >
                      {entry.is_dir ? "📁" : "📄"} {entry.name}
                    </button>
                  );
                })}
              </>
            )}
        </div>
        <div className={classes.pickerFooter}>
          <span className={classes.pickerSelected}>{path ?? "—"}</span>
          <div style={{ display: "flex", gap: 8 }}>
            <Button variant="ghost" onClick={onCancel}>취소</Button>
            {mode === "dir" && (
              <Button
                variant="primary"
                disabled={!canSelectCurrent}
                onClick={() => path && onSelect(path)}
              >
                이 폴더 선택
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function CloseIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M3 3 L11 11 M11 3 L3 11" strokeLinecap="round" />
    </svg>
  );
}
