import { useEffect, useMemo, useState } from "react";

import { Button } from "../../components/Button";
import { IconButton } from "../../components/IconButton";
import { Input } from "../../components/Input";
import { listPythonInterpreters, type PythonInterpreterMeta } from "../../api/system";
import { describeError } from "../../utils/errors";
import classes from "./services.module.css";
import componentClasses from "../../components/components.module.css";
import logsClasses from "../logs/logs.module.css";

export interface PythonPickerProps {
  open: boolean;
  onClose: () => void;
  onSelect: (path: string) => void;
}

/**
 * 시스템에 설치된 Python 인터프리터를 모달 리스트로 보여주고, 클릭 시 path를
 * 호출자에게 넘긴다. 호출자(ActionGroupForm 등)는 그 path로 command[0]을 교체한다.
 */
export function PythonPicker({ open, onClose, onSelect }: PythonPickerProps) {
  const [interpreters, setInterpreters] = useState<PythonInterpreterMeta[] | null>(null);
  const [error, setError] = useState<string | undefined>();
  const [search, setSearch] = useState("");

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    // 모달이 열릴 때마다 한 번 fetch. 짧은 cascading render는 의도적임.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setInterpreters(null);
    setError(undefined);
    void listPythonInterpreters()
      .then((res) => {
        if (cancelled) return;
        setInterpreters(res.interpreters);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(describeError(err));
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const filtered = useMemo(() => {
    if (!interpreters) return [];
    const q = search.trim().toLowerCase();
    if (!q) return interpreters;
    return interpreters.filter(
      (i) => i.path.toLowerCase().includes(q) || i.version.includes(q),
    );
  }, [interpreters, search]);

  if (!open) return null;

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
        style={{ maxWidth: "640px", width: "92vw" }}
      >
        <div className={componentClasses.modalHeader}>
          Python 인터프리터 선택
          <IconButton label="닫기" onClick={onClose} className={logsClasses.headerCloseInline}>
            <CloseIcon />
          </IconButton>
        </div>
        <div className={componentClasses.modalBody} style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
          <Input
            placeholder="경로 또는 버전 검색"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
          />
          {error && (
            <div style={{ color: "var(--status-error)", fontSize: "var(--font-sm)" }}>
              {error}
            </div>
          )}
          {!interpreters && !error && (
            <div style={{ color: "var(--text-muted)", fontSize: "var(--font-sm)" }}>
              로딩 중…
            </div>
          )}
          {interpreters && filtered.length === 0 && (
            <div style={{ color: "var(--text-muted)", fontSize: "var(--font-sm)" }}>
              검색 결과가 없습니다.
            </div>
          )}
          {filtered.length > 0 && (
            <ul style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
              {filtered.map((interp) => (
                <li key={interp.path}>
                  <button
                    type="button"
                    className={classes.pythonInterpRow}
                    onClick={() => onSelect(interp.path)}
                  >
                    <div className={classes.pythonInterpMeta}>
                      <span className={classes.pythonInterpPath}>{interp.path}</span>
                      {interp.note && (
                        <span className={classes.pythonInterpNote}>{interp.note}</span>
                      )}
                    </div>
                    <span className={classes.pythonInterpVersion}>{interp.version}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className={componentClasses.modalFooter}>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
        </div>
      </div>
    </div>
  );
}

function CloseIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
    >
      <path d="M3 3 L11 11 M11 3 L3 11" strokeLinecap="round" />
    </svg>
  );
}
