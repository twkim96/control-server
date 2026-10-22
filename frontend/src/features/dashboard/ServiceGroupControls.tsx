import { useEffect, useRef, useState } from "react";

import { startServiceGroupAction, getServiceGroupOperationJob } from "../../api/services";
import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import type {
  ServiceGroupAction,
  ServiceGroupOperationJob,
  ServiceMeta,
} from "../../types/service";
import { describeError } from "../../utils/errors";
import classes from "./dashboard.module.css";

const ACTION_LABEL: Record<ServiceGroupAction, string> = {
  start: "그룹 시작",
  stop: "그룹 중지",
  restart: "그룹 재시작",
};

export function ServiceGroupControls({
  group,
  services,
  supported,
  onMutated,
}: {
  group: string;
  services: ServiceMeta[];
  supported: boolean;
  onMutated: () => void;
}) {
  const [job, setJob] = useState<ServiceGroupOperationJob>();
  const [confirm, setConfirm] = useState<ServiceGroupAction>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string>();
  const onMutatedRef = useRef(onMutated);
  const active = job?.status === "queued" || job?.status === "running";
  const jobId = job?.id;

  useEffect(() => {
    onMutatedRef.current = onMutated;
  }, [onMutated]);

  useEffect(() => {
    if (!active || !jobId) return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const response = await getServiceGroupOperationJob(jobId);
        if (cancelled) return;
        setJob(response.job);
        setError(undefined);
        if (response.job.status !== "queued" && response.job.status !== "running") {
          onMutatedRef.current();
        } else {
          timer = window.setTimeout(() => void poll(), 1500);
        }
      } catch (err) {
        if (cancelled) return;
        setError(describeError(err));
        timer = window.setTimeout(() => void poll(), 3000);
      }
    };
    timer = window.setTimeout(() => void poll(), 1500);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [jobId, active]);

  const run = async (action: ServiceGroupAction) => {
    setSubmitting(true);
    setError(undefined);
    try {
      const response = await startServiceGroupAction(group, action);
      setJob(response.job);
      setConfirm(undefined);
      if (response.job.status !== "queued" && response.job.status !== "running") {
        onMutatedRef.current();
      }
    } catch (err) {
      setError(describeError(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className={classes.groupOperation} aria-label={`${group} 서비스 그룹`}>
      <div className={classes.groupOperationInfo}>
        <strong>{group}</strong>
        <span>{services.map((service) => service.name).join(" · ")}</span>
      </div>
      <div className={classes.groupOperationButtons}>
        {(["start", "stop", "restart"] as const).map((action) => {
          const needsConfirm = action !== "start";
          return (
            <Button
              key={action}
              size="sm"
              variant={action === "stop" ? "danger" : action === "start" ? "primary" : "default"}
              disabled={!supported || submitting || active}
              loading={submitting && !confirm && action === "start"}
              title={!supported ? "PM2 backend에서만 사용할 수 있습니다." : undefined}
              onClick={() => needsConfirm ? setConfirm(action) : void run(action)}
            >
              {ACTION_LABEL[action]}
            </Button>
          );
        })}
      </div>
      {!supported && (
        <div className={classes.groupOperationStatus}>
          그룹 제어는 PM2 backend에서만 사용할 수 있습니다.
        </div>
      )}
      {job && (
        <div className={classes.groupOperationStatus} role="status" aria-live="polite">
          <strong>
            {active
              ? `${ACTION_LABEL[job.action]} ${job.status === "queued" ? "대기 중" : "진행 중"}`
              : job.status === "succeeded" ? "그룹 작업 완료" : "그룹 작업 실패"}
          </strong>
          <span>{job.results.map((result) => `${result.service_id}: ${result.status}${result.message ? ` (${result.message})` : ""}`).join(" · ")}</span>
          {job.error && <span>{job.error}</span>}
          {error && <span>{error} · 작업 상태를 다시 확인합니다.</span>}
        </div>
      )}
      {confirm && (
        <ConfirmDialog
          open
          title={`${group} 그룹 ${confirm === "stop" ? "중지" : "재시작"}`}
          message={`그룹의 ${services.length}개 서비스를 ${confirm === "stop" ? "중지" : "재시작"}합니다. 상시 실행 서비스도 포함됩니다.`}
          confirmLabel={confirm === "stop" ? "그룹 중지" : "그룹 재시작"}
          variant="danger"
          loading={submitting}
          onCancel={() => setConfirm(undefined)}
          onConfirm={() => void run(confirm)}
        />
      )}
    </section>
  );
}
