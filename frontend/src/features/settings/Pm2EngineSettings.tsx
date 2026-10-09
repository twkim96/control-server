import { useCallback, useEffect, useRef, useState } from "react";

import {
  checkPm2EngineUpdate,
  getPm2EngineStatus,
  updatePm2Engine,
  type Pm2EngineStatus,
} from "../../api/system";
import { Button } from "../../components/Button";
import { usePolling } from "../../hooks/usePolling";
import { describeError } from "../../utils/errors";
import classes from "./settings.module.css";

export function Pm2EngineSettings() {
  const [status, setStatus] = useState<Pm2EngineStatus>();
  const [action, setAction] = useState<"check" | "update" | null>("check");
  const [actionError, setActionError] = useState<string>();
  const mounted = useRef(false);
  const checked = useRef(false);
  const requestId = useRef(0);

  // Ignore an older poll response if a newer action has already started.
  const request = useCallback(async (fetcher: () => Promise<Pm2EngineStatus>) => {
    const id = ++requestId.current;
    const next = await fetcher();
    if (mounted.current && id === requestId.current) setStatus(next);
    return next;
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const { error, refresh } = usePolling(
    () => request(getPm2EngineStatus),
    2500,
    action === null,
  );

  useEffect(() => {
    if (checked.current) return;
    checked.current = true;
    void request(getPm2EngineStatus)
      .then(() => request(checkPm2EngineUpdate))
      .catch((err: unknown) => {
        if (mounted.current) setActionError(describeError(err));
      })
      .finally(() => {
        if (mounted.current) setAction(null);
      });
  }, [request]);

  async function runAction(nextAction: "check" | "update") {
    setAction(nextAction);
    setActionError(undefined);
    try {
      await request(nextAction === "check" ? checkPm2EngineUpdate : updatePm2Engine);
    } catch (err) {
      if (mounted.current) setActionError(describeError(err));
    } finally {
      if (mounted.current) {
        setAction(null);
        void refresh();
      }
    }
  }

  const job = status?.job;
  const running = job?.status === "running";
  const busy = action !== null || running;
  const mismatch = status?.current_version && status.daemon_version &&
    status.current_version !== status.daemon_version;
  const failure = actionError || (error && "상태를 불러오지 못했습니다. 잠시 후 자동으로 다시 확인합니다.");

  return (
    <section className={`${classes.section} ${classes.pm2Section}`}>
      <header className={classes.sectionHeader}>
        <div>
          <h2 className={classes.sectionTitle}>PM2 엔진</h2>
          <p className={classes.sectionSub}>서비스 관리 엔진의 버전을 확인하고 업데이트합니다.</p>
        </div>
      </header>
      <div className={`${classes.panel} ${classes.pm2Panel}`}>
        <dl className={classes.pm2Versions}>
          <div><dt>설치 버전</dt><dd>{status?.current_version ?? (status ? "확인되지 않음" : "확인 중")}</dd></div>
          <div><dt>실행 중인 버전</dt><dd>{status?.daemon_version ?? "확인되지 않음"}</dd></div>
          <div><dt>최신 버전</dt><dd>{status?.latest_version ?? "확인 전"}</dd></div>
        </dl>
        {mismatch && <p className={classes.colorDescription}>설치 버전과 실행 중인 엔진 버전이 다릅니다. 업데이트하면 실행 중인 엔진에도 반영됩니다.</p>}
        {status?.checked_at && <p className={classes.colorDescription}>마지막 확인: {new Date(status.checked_at).toLocaleString("ko-KR")}</p>}
        {status && !status.supported && <p className={classes.colorDescription}>{status.reason || "이 환경에서는 PM2 엔진 업데이트를 지원하지 않습니다."}</p>}
        {status?.supported && status.latest_version && !status.update_available && !running && <p className={classes.colorDescription}>최신 버전을 사용 중입니다.</p>}
        {job && (
          <div className={classes.pm2Job} role="status" aria-live="polite" data-phase={job.phase}>
            <strong>{running ? "업데이트 진행 중" : job.status === "succeeded" ? "업데이트 완료" : "업데이트 실패"}</strong>
            <p>{job.message}</p>
            {job.error && <p>{job.error}</p>}
            {job.rolled_back && <p>이전 버전으로 복구했습니다.</p>}
          </div>
        )}
        {failure && <p className={classes.pm2Error} role="alert">{failure}</p>}
        <div className={classes.pm2Buttons}>
          <Button variant="ghost" onClick={() => void runAction("check")} disabled={busy || status?.supported === false} loading={action === "check"}>최신 버전 확인</Button>
          <Button variant="primary" onClick={() => void runAction("update")} disabled={busy || !status?.supported || !status.update_available} loading={action === "update" || running}>{running ? "업데이트 중" : "업데이트"}</Button>
        </div>
        <p className={classes.colorDescription}>업데이트 중 관리 서비스가 잠시 재시작될 수 있습니다.</p>
      </div>
    </section>
  );
}
