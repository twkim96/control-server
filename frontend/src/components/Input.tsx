import type { InputHTMLAttributes, ReactNode } from "react";

import classes from "./components.module.css";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  iconLeft?: ReactNode;
}

export function Input({ iconLeft, className, ...rest }: InputProps) {
  if (iconLeft) {
    return (
      <div className={[classes.inputWithIcon, className].filter(Boolean).join(" ")}>
        <span className={classes.inputIcon}>{iconLeft}</span>
        <input {...rest} className={classes.input} />
      </div>
    );
  }
  return <input {...rest} className={[classes.input, className].filter(Boolean).join(" ")} />;
}
