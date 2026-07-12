import { useEffect, useState } from "react";

import { Button } from "../../components/Button";
import classes from "./dashboard.module.css";

export interface ExternalKillDialogProps {
  open: boolean;
  serviceName: string;
  pid: number | null | undefined;
  loading: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ExternalKillDialog({
  open,
  serviceName,
  pid,
  loading,
  onCancel,
  onConfirm,
}: ExternalKillDialogProps) {
  const [copiedField, setCopiedField] = useState<"pid" | "cmd" | undefined>();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !loading) onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, loading, onCancel]);

  if (!open) return null;

  const killCommand = pid ? `kill ${pid}` : "—";

  const copy = async (text: string, field: "pid" | "cmd") => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedField(field);
      window.setTimeout(() => setCopiedField(undefined), 1500);
    } catch {
      // 클립보드 권한 없을 때 fallback (드물지만)
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        setCopiedField(field);
        window.setTimeout(() => setCopiedField(undefined), 1500);
      } finally {
        document.body.removeChild(ta);
      }
    }
  };

  return (
    <div
      className={classes.modalOverlay}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !loading) onCancel();
      }}
    >
      <div className={classes.modalCard} role="dialog" aria-modal="true">
        <div className={classes.modalHeader}>외부 인스턴스 종료</div>
        <div className={classes.modalBody}>
          <p className={classes.modalParagraph}>
            <strong>{serviceName}</strong>은(는) 컨트롤 서버가 띄우지 않은
            외부 프로세스입니다. 정말 종료할까요?
          </p>

          <div className={classes.modalKv}>
            <span className={classes.modalKvLabel}>PID</span>
            <code className={classes.modalKvValue}>{pid ?? "—"}</code>
            <button
              type="button"
              className={classes.modalCopyBtn}
              onClick={() => pid && copy(String(pid), "pid")}
              disabled={pid == null}
            >
              {copiedField === "pid" ? "복사됨" : "복사"}
            </button>
          </div>

          <div className={classes.modalKv}>
            <span className={classes.modalKvLabel}>kill 명령</span>
            <code className={classes.modalKvValue}>{killCommand}</code>
            <button
              type="button"
              className={classes.modalCopyBtn}
              onClick={() => pid && copy(killCommand, "cmd")}
              disabled={pid == null}
            >
              {copiedField === "cmd" ? "복사됨" : "복사"}
            </button>
          </div>

          <p className={classes.modalParagraphMuted}>
            "강제 종료"를 누르면 컨트롤 서버가 SIGINT → SIGTERM → SIGKILL 순으로
            시그널을 보냅니다. 정상 종료 핸들러가 있는 서버는 SIGINT만으로
            깨끗히 정리됩니다.
          </p>
        </div>
        <div className={classes.modalFooter}>
          <Button variant="ghost" onClick={onCancel} disabled={loading}>
            취소
          </Button>
          <Button variant="danger" onClick={onConfirm} loading={loading}>
            강제 종료
          </Button>
        </div>
      </div>
    </div>
  );
}
