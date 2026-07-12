import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import classes from "./components.module.css";
import { ToastContext, type ToastVariant } from "./toast-context";

interface ToastItem {
  id: number;
  message: string;
  variant: ToastVariant;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const idRef = useRef(0);

  const push = useCallback((message: string, variant: ToastVariant = "default") => {
    const id = ++idRef.current;
    setItems((prev) => [...prev, { id, message, variant }]);
    window.setTimeout(() => {
      setItems((prev) => prev.filter((t) => t.id !== id));
    }, 3500);
  }, []);

  const api = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className={classes.toastContainer}>
        {items.map((t) => {
          const variantCls = t.variant === "default" ? "" : classes[t.variant];
          return (
            <div key={t.id} className={[classes.toast, variantCls].filter(Boolean).join(" ")}>
              {t.message}
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}
