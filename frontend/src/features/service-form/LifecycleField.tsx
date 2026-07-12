import type {
  LifecycleMeta,
  LifecycleMode,
  UnmanagedPolicy,
  Visibility,
} from "../../types/service";
import classes from "./service-form.module.css";

export interface LifecycleFieldProps {
  value: LifecycleMeta;
  onChange: (next: LifecycleMeta) => void;
}

const MODE_OPTIONS: Array<{ value: LifecycleMode; label: string; help: string }> = [
  {
    value: "manual",
    label: "수동 실행",
    help: "필요할 때 시작/중지하는 일반 서비스",
  },
  {
    value: "always_on",
    label: "상시 실행",
    help: "끄지 않는 서비스. 중지는 위험 작업으로 표시",
  },
];

const VISIBILITY_OPTIONS: Array<{ value: Visibility; label: string }> = [
  { value: "primary", label: "기본 버튼" },
  { value: "danger_menu", label: "더보기 메뉴 (위험)" },
  { value: "hidden", label: "숨김" },
];

const UNMANAGED_OPTIONS: Array<{ value: UnmanagedPolicy; label: string; help: string }> = [
  {
    value: "status_only",
    label: "표시만",
    help: "외부에서 같은 포트로 떠있으면 'External' 상태로 표시. 정리는 '외부 인스턴스 종료'로 별도.",
  },
  {
    value: "manage",
    label: "자동 추적",
    help: "외부 인스턴스가 config(command/port)와 일치하면 자동으로 추적 대상으로 받아들여 일반 서비스처럼 관리.",
  },
];

export function LifecycleField({ value, onChange }: LifecycleFieldProps) {
  return (
    <div className={classes.field}>
      <div className={classes.label}>실행 방식</div>
      <select
        value={value.mode}
        onChange={(e) =>
          onChange({ ...value, mode: e.target.value as LifecycleMode })
        }
        className={classes.input}
        style={{
          minHeight: "var(--control-height-md)",
          padding: "0 12px",
          backgroundColor: "var(--bg-input)",
          border: "1px solid var(--border-default)",
          borderRadius: "var(--radius-md)",
          color: "var(--text-primary)",
          fontSize: "var(--font-sm)",
        }}
      >
        {MODE_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <div className={classes.help}>
        {MODE_OPTIONS.find((o) => o.value === value.mode)?.help}
      </div>

      <label className={classes.checkboxRow} style={{ marginTop: 8 }}>
        <input
          type="checkbox"
          checked={value.autostart}
          onChange={(e) => onChange({ ...value, autostart: e.target.checked })}
        />
        컨트롤 서버 시작 시 자동으로 켜기
      </label>

      <div className={classes.fieldRow} style={{ marginTop: 8 }}>
        <div className={classes.field}>
          <div className={classes.label}>중지 버튼 위치</div>
          <select
            value={value.stop_visibility}
            onChange={(e) =>
              onChange({ ...value, stop_visibility: e.target.value as Visibility })
            }
            style={selectStyle}
          >
            {VISIBILITY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div className={classes.field}>
          <div className={classes.label}>재시작 버튼 위치</div>
          <select
            value={value.restart_visibility}
            onChange={(e) =>
              onChange({ ...value, restart_visibility: e.target.value as Visibility })
            }
            style={selectStyle}
          >
            {VISIBILITY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className={classes.field} style={{ marginTop: 8 }}>
        <div className={classes.label}>외부 인스턴스 정책</div>
        <select
          value={value.unmanaged_policy}
          onChange={(e) =>
            onChange({ ...value, unmanaged_policy: e.target.value as UnmanagedPolicy })
          }
          style={selectStyle}
        >
          {UNMANAGED_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <div className={classes.help}>
          {UNMANAGED_OPTIONS.find((o) => o.value === value.unmanaged_policy)?.help}
        </div>
      </div>
    </div>
  );
}

const selectStyle: React.CSSProperties = {
  minHeight: "var(--control-height-md)",
  padding: "0 12px",
  backgroundColor: "var(--bg-input)",
  border: "1px solid var(--border-default)",
  borderRadius: 6,
  color: "var(--text-primary)",
  fontSize: "var(--font-sm)",
};
