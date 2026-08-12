import { defineConfig, loadEnv } from "vite";

export function normalizeBasePath(value: string | undefined): string {
  const basePath = value?.trim();

  if (!basePath || basePath === "/") {
    return "/";
  }

  if (basePath === "." || basePath === "./") {
    return "./";
  }

  const withLeadingSlash = basePath.startsWith("/") ? basePath : `/${basePath}`;
  return withLeadingSlash.endsWith("/") ? withLeadingSlash : `${withLeadingSlash}/`;
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");

  return {
    base: normalizeBasePath(env.BASE_PATH),
    build: {
      outDir: "dist",
      emptyOutDir: true,
    },
    preview: {
      host: "127.0.0.1",
      port: 4173,
    },
    server: {
      host: "127.0.0.1",
      port: 5173,
      // 把 /api/* 轉到後端 FastAPI（同源、免 CORS）；FetchDashboardSource 走相對路徑 /api/v1。
      // 後端位址可用環境變數 BACKEND_ORIGIN 覆蓋。
      // ⚠️ 預設必須寫 127.0.0.1 不能寫 localhost：Node ≥17 會把 localhost 優先解析成
      // IPv6 ::1，而 uvicorn 預設只監聽 IPv4 → proxy 連不上時 Vite 靜默回 index.html
      // （HTTP 200 但 content-type=text/html），前端誤判成資料格式錯而退回 mock，極難察覺。
      proxy: {
        "/api": {
          target: process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8000",
          changeOrigin: true,
        },
      },
    },
  };
});
