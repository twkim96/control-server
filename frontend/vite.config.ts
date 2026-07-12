import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 개발 시:
// - /api/*는 Flask(127.0.0.1:9000)로 proxy
// - SSE 응답을 자르지 않기 위해 ws:false, configure에서 keep-alive 명시
// 환경변수:
// - VITE_API_BASE: 비워두면 same-origin (proxy 경유). 다른 호스트로 띄울 때만 사용.
// - VITE_DEV_CONTROL_TOKEN: dev 모드에서 mutation 호출에 자동 부착되는 토큰 (api/client.ts에서 사용).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backendUrl = env.VITE_BACKEND_URL || "http://127.0.0.1:9000";

  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: false,
      proxy: {
        "/api": {
          target: backendUrl,
          changeOrigin: true,
          // SSE는 단순한 long-lived HTTP. ws:false로 두고 기본 http proxy 사용.
        },
      },
    },
    build: {
      outDir: "dist",
      sourcemap: true,
    },
  };
});
