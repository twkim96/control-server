import type { ButtonHTMLAttributes, ReactNode } from "react";

import classes from "./components.module.css";

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  children: ReactNode;
}

export function IconButton({ label, children, className, ...rest }: IconButtonProps) {
  const cls = [classes.iconButton, className].filter(Boolean).join(" ");
  return (
    <button type="button" aria-label={label} title={label} {...rest} className={cls}>
      {children}
    </button>
  );
}
