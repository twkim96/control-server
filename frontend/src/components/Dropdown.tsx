import { useEffect, useRef, useState, type ReactNode } from "react";

import classes from "./components.module.css";

export interface DropdownItem {
  id: string;
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  variant?: "default" | "danger";
}

export interface DropdownGroup {
  items: DropdownItem[];
}

export interface DropdownProps {
  trigger: ReactNode;
  groups: DropdownGroup[];
  // 외부에서 열림 상태를 통제하지 않을 때만 사용한다.
  align?: "left" | "right";
}

export function Dropdown({ trigger, groups, align = "right" }: DropdownProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (!wrapRef.current) return;
      if (!wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const menuStyle = align === "left" ? { right: "auto", left: 0 } : undefined;

  return (
    <div className={classes.dropdownWrap} ref={wrapRef}>
      <div onClick={() => setOpen((v) => !v)}>{trigger}</div>
      {open && (
        <div className={classes.dropdownMenu} style={menuStyle} role="menu">
          {groups.map((group, gi) => (
            <div key={gi}>
              {gi > 0 && <div className={classes.dropdownDivider} />}
              {group.items.map((item) => {
                const variantCls = item.variant === "danger" ? classes.danger : "";
                const cls = [classes.dropdownItem, variantCls].filter(Boolean).join(" ");
                return (
                  <button
                    key={item.id}
                    type="button"
                    role="menuitem"
                    className={cls}
                    disabled={item.disabled}
                    onClick={() => {
                      setOpen(false);
                      item.onSelect();
                    }}
                  >
                    {item.label}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
