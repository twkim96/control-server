import { createContext } from "react";

export type ToastVariant = "default" | "success" | "error";

export interface ToastApi {
  push: (message: string, variant?: ToastVariant) => void;
}

export const ToastContext = createContext<ToastApi | null>(null);
