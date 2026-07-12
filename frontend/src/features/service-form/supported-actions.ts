import type { ActionType } from "../../types/service";

export interface ActionDef {
  type: ActionType;
  id: string;
  label: string;
}

export const SUPPORTED_ACTIONS: ActionDef[] = [
  { type: "process_start", id: "start", label: "시작" },
  { type: "process_stop", id: "stop", label: "중지" },
  { type: "process_restart", id: "restart", label: "재시작" },
  { type: "health_check", id: "health", label: "상태 확인" },
  { type: "show_logs", id: "logs", label: "로그" },
  { type: "open_url", id: "open", label: "URL 열기" },
];
