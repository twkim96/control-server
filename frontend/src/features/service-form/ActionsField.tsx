import type { ActionType } from "../../types/service";
import { SUPPORTED_ACTIONS } from "./supported-actions";
import classes from "./service-form.module.css";

export interface ActionsFieldProps {
  selected: Set<ActionType>;
  onChange: (next: Set<ActionType>) => void;
}

export function ActionsField({ selected, onChange }: ActionsFieldProps) {
  const toggle = (type: ActionType) => {
    const next = new Set(selected);
    if (next.has(type)) next.delete(type);
    else next.add(type);
    onChange(next);
  };

  return (
    <div className={classes.field}>
      <div className={classes.label}>사용할 기능</div>
      <div className={classes.actionsList}>
        {SUPPORTED_ACTIONS.map((a) => (
          <label key={a.type} className={classes.checkboxRow}>
            <input
              type="checkbox"
              checked={selected.has(a.type)}
              onChange={() => toggle(a.type)}
            />
            {a.label}
          </label>
        ))}
      </div>
      <div className={classes.help}>
        체크하지 않은 기능은 대시보드에서 버튼이 만들어지지 않습니다.
      </div>
    </div>
  );
}
