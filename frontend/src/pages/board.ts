import {
  escapeHtml,
  renderBreadcrumb,
  renderGlobalPulse,
  renderIndustries,
  renderMarketDetail,
  renderTaiwanBreadth,
  renderTurnoverBubbles,
  renderTerritoryWar,
  mountTerritoryWar,
} from "../components";
import type { DashboardSnapshot, Dispose } from "../types";

/** 資料狀態燈號。能畫出這一頁就代表五塊都取到真資料——取不到會走 startup-error 頁。 */
function renderTrust(): string {
  return ["全球脈動", "台股收盤", "產業表現", "成交熱度 · 領土戰"]
    .map((t) => `<li class="is-real"><i></i>${escapeHtml(t)}</li>`).join("");
}

function renderSourceStatus(snapshot: DashboardSnapshot): string {
  return snapshot.sourceStatuses
    .map(
      (source) => `
        <span><i class="source-dot source-dot--${source.status}"></i>
          ${escapeHtml(source.label)} <em>${escapeHtml(source.asOf)}</em>
        </span>`,
    )
    .join("");
}

export function renderBoard(snapshot: DashboardSnapshot): string {
  const breadth = snapshot.taiwanBreadth;

  return `<div class="board-page">
    <div class="board-shell">
      ${renderBreadcrumb([{ t: "本期", href: "#home" }, { t: "今日大局" }])}
      <header class="board-header">
        <div class="board-header__title">
          <p class="dash-kicker">TODAY'S BIG PICTURE · ${escapeHtml(snapshot.marketDate)}</p>
          <h1>今日大局</h1>
          <p class="board-header__insight">${escapeHtml(snapshot.insight)}</p>
        </div>
        <div class="board-header__trust">
          <p class="dash-kicker">資料狀態</p>
          <ul class="board-trust">${renderTrust()}</ul>
        </div>
        <div class="board-header__metrics">
          <span><small>TAIEX</small><b>${breadth.indexClose.toLocaleString("en-US")}</b><em class="${breadth.indexChange1d >= 0 ? "up" : "down"}">${breadth.indexChange1d >= 0 ? "+" : ""}${breadth.indexChange1d.toFixed(2)}%</em></span>
          <span><small>上 / 下跌家數</small><b>${breadth.advancers} / ${breadth.decliners}</b></span>
          <span><small>成交值 vs 20D</small><b>${breadth.turnoverVs20d.toFixed(2)}×</b></span>
          <span><small>資料覆蓋</small><b>${Math.round(snapshot.dataCoverage * 100)}%</b></span>
        </div>
      </header>

      <main class="dashboard">
        ${renderTerritoryWar(snapshot.taiwanBreadth)}
        ${renderGlobalPulse(snapshot.globalMarkets)}
        ${renderTaiwanBreadth(snapshot.taiwanBreadth)}
        ${renderIndustries(snapshot.industries)}
        ${renderTurnoverBubbles(snapshot.topTurnover)}
      </main>

      <footer class="board-data-note">
        <div><b>資料狀態</b>${renderSourceStatus(snapshot)}</div>
        <p>本頁五個區塊全部為每日收盤真實資料，無模擬內容；取不到資料時本頁不會顯示。領土戰的領土比例＝當日漲跌家數，交界處的推擠僅為視覺呈現。本頁不代表即時行情或投資建議。最後產生：${escapeHtml(snapshot.generatedAt)}</p>
      </footer>
    </div>
  </div>`;
}

export function mountBoard(
  root: HTMLElement,
  snapshot: DashboardSnapshot,
): Dispose {
  const board = root.querySelector<HTMLElement>(".board-page");
  const detail = root.querySelector<HTMLElement>("[data-market-detail]");
  const firstMarket = snapshot.globalMarkets[0];

  if (detail && firstMarket) {
    detail.innerHTML = renderMarketDetail(firstMarket);
  }

  const selectMarket = (marketId: string): void => {
    const market = snapshot.globalMarkets.find((candidate) => candidate.id === marketId);
    if (!market || !detail) return;
    root.querySelectorAll<HTMLElement>("[data-market]").forEach((element) => {
      element.setAttribute("aria-pressed", String(element.dataset.market === marketId));
    });
    detail.innerHTML = renderMarketDetail(market);
  };

  // 選取列已移除，點個股只剩「我正在看這一顆」的視覺標記——泡泡上本來就有
  // 名稱、漲跌與成交值，原本那條檢視器等於把同樣的數字再抄一遍。
  const selectStock = (ticker: string): void => {
    root.querySelectorAll<HTMLElement>("[data-stock]").forEach((element) => {
      const selected = element.dataset.stock === ticker;
      element.toggleAttribute("data-selected", selected);
      element.setAttribute("aria-pressed", String(selected));
    });
  };

  const onClick = (event: MouseEvent): void => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const marketTarget = target.closest<HTMLElement>("[data-market]");
    if (marketTarget?.dataset.market) {
      selectMarket(marketTarget.dataset.market);
      return;
    }
    const stockTarget = target.closest<HTMLElement>("[data-stock]");
    if (stockTarget?.dataset.stock) {
      selectStock(stockTarget.dataset.stock);
    }
  };

  board?.addEventListener("click", onClick);

  const disposeWar = board ? mountTerritoryWar(board, snapshot.taiwanBreadth) : () => undefined;

  return () => {
    board?.removeEventListener("click", onClick);
    disposeWar();
  };
}
