// 화면에서 입력해 백엔드 config_writer로 보내는 등록/수정 페이로드.
// 서비스 메타데이터와 형태는 같지만 runtime이 없고 일부 필드는 선택값.

import type {
  ActionType,
  HealthMeta,
  HttpsMeta,
  LifecycleMeta,
  LogMeta,
  StopStrategyMeta,
} from "./service";

export interface ServicePayload {
  id: string;
  name: string;
  description?: string;
  cwd: string;
  entry_file: string;
  command: string[];
  adopt_command?: string[] | null;
  adopt_match?: "exact" | "prefix";
  env?: Record<string, string>;
  port?: number | null;
  port_env_name?: string | null;
  open_url?: string | null;
  https?: HttpsMeta | null;
  health?: HealthMeta | null;
  log?: LogMeta | null;
  lifecycle?: LifecycleMeta | null;
  actions?: Array<{
    id: string;
    label: string;
    type: ActionType;
    enabled?: boolean;
    strategy?: StopStrategyMeta;
  }>;
}

export interface FileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number | null;
  mtime: number | null;
}

export interface FileListResponse {
  path?: string;
  roots?: FileEntry[];
  entries: FileEntry[];
}
