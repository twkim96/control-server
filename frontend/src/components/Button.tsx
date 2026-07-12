import type { ButtonHTMLAttributes, ReactNode } from "react";

import classes from "./components.module.css";

type Variant = "default" | "primary" | "danger" | "ghost";
type Size = "sm" | "md" | "lg";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  iconLeft?: ReactNode;
  iconRight?: ReactNode;
}

export function Button({
  variant = "default",
  size = "md",
  loading,
  iconLeft,
  iconRight,
  children,
  className,
  disabled,
  ...rest
}: ButtonProps) {
  const variantClass = variant === "default" ? "" : classes[variant];
  const sizeClass = size === "md" ? "" : classes[size];
  const cls = [classes.button, variantClass, sizeClass, className]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      type="button"
      {...rest}
      className={cls}
      disabled={disabled || loading}
    >
      {loading ? <span className={classes.spinner} aria-hidden /> : iconLeft}
      {children}
      {iconRight}
    </button>
  );
}
