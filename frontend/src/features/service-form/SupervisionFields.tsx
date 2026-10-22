import { Input } from "../../components/Input";
import type { RecoveryMeta, RestartMeta, ServiceMeta } from "../../types/service";
import type { BasicFields } from "./ServiceFormFields";
import classes from "./service-form.module.css";

export interface SupervisionFieldsProps {
  basic: BasicFields;
  services: ServiceMeta[];
  restart: RestartMeta;
  recovery: RecoveryMeta;
  onBasicChange: (patch: Partial<BasicFields>) => void;
  onRestartChange: (next: RestartMeta) => void;
  onRecoveryChange: (next: RecoveryMeta) => void;
  healthEnabled: boolean;
}

export function SupervisionFields({
  basic,
  services,
  restart,
  recovery,
  onBasicChange,
  onRestartChange,
  onRecoveryChange,
  healthEnabled,
}: SupervisionFieldsProps) {
  const dependencies = services.filter((service) => service.id !== basic.id);
  const groups = [...new Set(services.map((service) => service.group).filter(Boolean))];

  const toggleDependency = (serviceId: string, checked: boolean) => {
    const next = new Set(basic.depends_on);
    if (checked) next.add(serviceId);
    else next.delete(serviceId);
    onBasicChange({ depends_on: [...next] });
  };

  return (
    <section className={classes.section}>
      <div className={classes.sectionTitle}>그룹 및 의존성</div>
      <div className={classes.field}>
        <label className={classes.label} htmlFor="service-group">서비스 그룹</label>
        <Input
          id="service-group"
          value={basic.group}
          onChange={(event) => onBasicChange({ group: event.target.value })}
          placeholder="예: backend"
          list="service-group-options"
        />
        <datalist id="service-group-options">
          {groups.map((group) => <option key={group} value={group} />)}
        </datalist>
        <div className={classes.help}>같은 그룹의 서비스를 함께 시작·중지·재시작합니다.</div>
      </div>

      <fieldset className={classes.dependencyFieldset}>
        <legend className={classes.label}>시작 전에 준비되어야 하는 서비스</legend>
        {dependencies.length === 0 ? (
          <div className={classes.help}>선택할 다른 서비스가 없습니다.</div>
        ) : (
          <div className={classes.dependencyList}>
            {dependencies.map((service) => (
              <label className={classes.checkboxRow} key={service.id}>
                <input
                  type="checkbox"
                  checked={basic.depends_on.includes(service.id)}
                  onChange={(event) => toggleDependency(service.id, event.target.checked)}
                />
                {service.name} <span className={classes.help}>({service.id})</span>
              </label>
            ))}
          </div>
        )}
      </fieldset>

      <details className={classes.supervisionDetails}>
        <summary>재시작 및 헬스 복구 고급 설정</summary>
        <div className={classes.supervisionBody}>
          <div className={classes.sectionTitle}>자동 재시작</div>
          <label className={classes.checkboxRow}>
            <input
              type="checkbox"
              checked={restart.autorestart}
              onChange={(event) => onRestartChange({ ...restart, autorestart: event.target.checked })}
            />
            프로세스 종료 시 자동 재시작
          </label>
          <div className={classes.fieldRow}>
            <NumberField
              label="최소 실행 시간 (초)"
              value={restart.min_uptime_seconds}
              min={0.001}
              step="any"
              onChange={(value) => onRestartChange({ ...restart, min_uptime_seconds: value })}
            />
            <NumberField
              label="불안정한 시작의 최대 재시도 횟수"
              help="최소 실행 시간 전에 종료되는 시작이 반복될 때 적용되는 재시도 한도입니다."
              value={restart.max_restarts}
              min={1}
              onChange={(value) => onRestartChange({ ...restart, max_restarts: value })}
            />
          </div>
          <div className={classes.fieldRow}>
            <NumberField
              label="재시작 지연 (ms)"
              value={restart.restart_delay_ms}
              min={0}
              onChange={(value) => onRestartChange({ ...restart, restart_delay_ms: value })}
            />
            <NumberField
              label="지수 backoff 시작 지연 (ms)"
              value={restart.exp_backoff_restart_delay_ms}
              min={0}
              onChange={(value) => onRestartChange({ ...restart, exp_backoff_restart_delay_ms: value })}
            />
          </div>
          <NumberField
            label="메모리 초과 재시작 (MB, 0은 끔)"
            help="최상위 프로세스의 메모리 초과 시 재시작을 요청합니다. 사용량 자체를 제한하지 않으며 자식 프로세스에는 적용되지 않습니다."
            value={restart.max_memory_restart_mb}
            min={0}
            onChange={(value) => onRestartChange({ ...restart, max_memory_restart_mb: value })}
          />

          <div className={classes.sectionTitle}>헬스 실패 복구</div>
          <label className={classes.checkboxRow}>
            <input
              type="checkbox"
              checked={recovery.enabled}
              onChange={(event) => onRecoveryChange({ ...recovery, enabled: event.target.checked })}
            />
            헬스 검사 실패 후 복구 재시작 사용
          </label>
          {!healthEnabled && (
            <div className={classes.help}>헬스 endpoint를 켜야 복구 재시작을 사용할 수 있습니다.</div>
          )}
          <div className={classes.fieldRow}>
            <NumberField
              label="연속 실패 횟수"
              value={recovery.failure_threshold}
              min={1}
              onChange={(value) => onRecoveryChange({ ...recovery, failure_threshold: value })}
            />
            <NumberField
              label="시작 유예 시간 (초)"
              value={recovery.startup_grace_seconds}
              min={0}
              step="any"
              onChange={(value) => onRecoveryChange({ ...recovery, startup_grace_seconds: value })}
            />
          </div>
          <div className={classes.fieldRow}>
            <NumberField
              label="복구 cooldown (초)"
              value={recovery.cooldown_seconds}
              min={0}
              step="any"
              onChange={(value) => onRecoveryChange({ ...recovery, cooldown_seconds: value })}
            />
            <NumberField
              label="시간당 최대 재시작 횟수"
              value={recovery.max_restarts_per_hour}
              min={1}
              onChange={(value) => onRecoveryChange({ ...recovery, max_restarts_per_hour: value })}
            />
          </div>
        </div>
      </details>
    </section>
  );
}

function NumberField({
  label,
  help,
  value,
  min,
  step = 1,
  onChange,
}: {
  label: string;
  help?: string;
  value: number;
  min: number;
  step?: number | "any";
  onChange: (value: number) => void;
}) {
  return (
    <div className={classes.field}>
      <label className={classes.label}>{label}</label>
      <Input
        type="number"
        value={value}
        min={min}
        step={step}
        onChange={(event) => onChange(event.target.value === "" ? Number.NaN : Number(event.target.value))}
        onWheel={(event) => (event.target as HTMLInputElement).blur()}
      />
      {help && <div className={classes.help}>{help}</div>}
    </div>
  );
}
