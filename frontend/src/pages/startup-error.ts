import { escapeHtml } from "../components";

/**
 * 啟動失敗頁。
 *
 * 拿掉 mock 退路之後，取不到資料就沒有東西可畫——但 main.ts 原本只有
 * `console.error`，實測結果是**整片白畫面**，使用者連「壞了」都看不出來。
 * 假資料不能給，白畫面也不能給，所以要有這一頁。
 *
 * 寫給兩種人看：使用者要知道「不是我的問題、等一下再來」，
 * 開發者要知道「哪一支端點、什麼錯」——所以原始訊息照實顯示，不美化。
 */
export function renderStartupError(error: unknown): string {
  const detail = error instanceof Error ? error.message : String(error);
  return `<div class="page-wrap">
    <section class="section section--top">
      <p class="section__eyebrow">SERVICE UNAVAILABLE</p>
      <div class="section__head"><h1 class="section__title">資料暫時取不到</h1></div>
      <div class="startup-error acrylic">
        <p>本站的數字全部來自每日收盤的真實資料。目前後端沒有回應，
          為避免顯示過期或不實的數字，我們選擇不顯示任何內容。</p>
        <p class="startup-error__hint">通常是後端服務或資料庫暫時中斷，稍後重新整理即可。</p>
        <details>
          <summary>技術細節</summary>
          <pre>${escapeHtml(detail)}</pre>
        </details>
        ${import.meta.env.DEV ? `<div class="startup-error__dev">
          <b>開發環境排查（這段只在 dev 顯示）</b>
          <ol>
            <li>後端起了嗎？<code>cd repo 根目錄 && uv run python -m uvicorn app.main:app --port 8000</code></li>
            <li><code>.env</code> 有 DATABASE_URL 嗎？（向組長拿，範本見 .env.example）</li>
            <li>確認 <code>http://localhost:8000/health</code> 回 {"status":"ok","db":"ok"}</li>
          </ol>
          <p>細節見根目錄 README「快速開始」。這一頁本身是正常設計：本站拿不到真資料時不以模擬值替代。</p>
        </div>` : ""}
        <button type="button" class="btn" data-retry>重新載入</button>
      </div>
    </section>
  </div>`;
}

export function mountStartupError(root: HTMLElement): () => void {
  const retry = root.querySelector<HTMLButtonElement>("[data-retry]");
  const onClick = (): void => location.reload();
  retry?.addEventListener("click", onClick);
  return () => retry?.removeEventListener("click", onClick);
}
