import type { ActionMeta } from "../../types/service";

export function shouldConfirmAction(action: ActionMeta): boolean {
  return action.type === "process_stop" || action.type === "process_restart";
}

export function getActionConfirmCopy(serviceName: string, action: ActionMeta) {
  if (action.type === "process_restart") {
    return {
      title: `${serviceName} 재시작`,
      message:
        `${serviceName} 서비스를 재시작합니다. 현재 프로세스가 중지된 뒤 다시 시작되며, 연결이 잠시 끊길 수 있습니다.`,
      confirmLabel: "재시작",
    };
  }

  return {
    title: `${serviceName} 중지`,
    message:
      `${serviceName} 서비스를 중지합니다. 실행 중인 작업이나 접속이 끊길 수 있습니다.`,
    confirmLabel: "중지",
  };
}
