// 백엔드의 표준 에러 응답 모양.
// routes/api.py와 routes/config_api.py에서 jsonify되어 내려간다.
export interface ApiError {
  error: string;
  message?: string;
  [extra: string]: unknown;
}

export interface MetaResponse {
  ok: true;
  password_configured: boolean;
  controller: { host: string; port: number };
  service_count: number;
}
