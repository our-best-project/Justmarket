/**
 * API 位址的唯一來源。
 *
 * 預設是相對路徑 `/api/v1`：本機開發時 vite dev proxy 會把它轉到
 * http://127.0.0.1:8000（同源、免 CORS），前後端同域部署時也直接成立。
 *
 * GitHub Pages 是靜態站，沒有 proxy 可用，前端與 API 一定跨網域 —— 那時用
 * build 期的 `VITE_API_BASE` 指到公開 API（例：https://xxx.run.app/api/v1），
 * 並在後端把 Pages 的 origin 加進 `CORS_EXTRA_ORIGINS`。
 *
 * 為什麼是 build 期而不是執行期讀設定：Pages 上沒有伺服器可以注入設定，
 * 而多做一次「先抓 config.json 再抓資料」只是把同一個問題往後推一層。
 */
const configured = import.meta.env.VITE_API_BASE as string | undefined;

export const API_BASE = (configured?.trim() || "/api/v1").replace(/\/+$/, "");
