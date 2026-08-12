# web — 前端

Vanilla TypeScript + Vite + Three.js 的單頁應用。沒有前端框架：頁面是純函式
`(state) => html`，掛載後回傳 dispose。這個選擇讓整份程式碼可以逐檔讀完，
也讓 bundle 裡沒有一行是為了框架而存在的。

## 怎麼跑

```bash
npm ci && npm run dev
```

dev server 在 5173，`/api/*` 會被代理到 `http://127.0.0.1:8000`（同源、免 CORS），
所以本機要另外起一個後端：`uv run python -m eventsignal api`。

```bash
npm run check
```

typecheck + eslint（`--max-warnings=0`）+ vitest + 循環依賴檢查，一次跑完。

## 讀的順序

1. `src/main.ts` —— 組裝：把資料源、watchlist、視覺效果接上 app
2. `src/app.ts` —— shell、路由切換、生命週期
3. `src/types.ts` —— 前端使用的資料形狀，等同前端這側的 API 契約
4. `src/data/` —— 與後端之間的介面
5. `src/pages/` —— 各路由畫的東西
6. `src/components/` —— 共用 UI 與 dashboard renderer
7. `src/visuals/` —— 背景視差與 Three.js 場景

## 目錄

| 路徑 | 負責 |
|---|---|
| `src/data/api-base.ts` | API 位址的唯一來源 |
| `src/data/event-source.ts`、`dashboard-source.ts` | 介面定義。UI 只依賴它們 |
| `src/data/fetch-*.ts` | 實作：打真 API |
| `src/pages/` | home／events／event-detail／search／watchlist／board／method／startup-error |
| `src/components/` | event-card、event-row、dashboard、reaction-chart、territory-war、market-narrative、breadcrumb |
| `src/visuals/` | parallax、workers-3d（Three.js）、scene-config |
| `src/services/watchlist.ts` | localStorage 追蹤清單 |
| `src/styles/` | tokens（設計變數）→ index → pages／dashboard／scene／responsive |

UI 只依賴 `EventSource` 與 `DashboardSource` 兩個非同步介面，不直接碰 `fetch`。
測試用 `tests/fixtures/` 的假資料實作同一組介面，所以單元測試不需要起後端。

## 取不到資料就報錯

**沒有 mock 退路。** 任一端點失敗就整頁顯示錯誤頁（`src/pages/startup-error.ts`）。

早期版本會靜默退回 mock，實測後果是：畫面顯示三週前的假數字、四顆資料狀態燈全亮
「真實」、頁尾還宣稱「無模擬內容」—— 使用者沒有任何辦法分辨。一個講市場資料的產品，
寧可什麼都不顯示，也不能顯示分不出真假的數字。

## 部署（GitHub Pages）

由根目錄的 `.github/workflows/pages.yml` 建置發佈。兩個 build 期變數：

| 變數 | 值 | 為什麼 |
|---|---|---|
| `BASE_PATH` | `/<repo>/`（workflow 自動帶） | Pages 掛在子路徑，資源路徑要有這一段 |
| `VITE_API_BASE` | repo variable `API_BASE` | 靜態站沒有 proxy，API 位址必須是絕對網址 |

本機建置一份 Pages 版來看看：

```bash
BASE_PATH=/Justmarket/ VITE_API_BASE=https://example.com/api/v1 npm run build
```

後端要把 Pages 的 origin 加進 `CORS_EXTRA_ORIGINS`，否則瀏覽器會擋。

## 測試

```bash
npm run test
```

單元測試涵蓋 router、資料邊界、event-card、reaction-chart、territory-war、
turnover-bubbles、market-narrative、watchlist、base-path。

```bash
npm run test:e2e
```

Playwright，用 `tests/fixtures/` 攔截 API，不需要真後端。
