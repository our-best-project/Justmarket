import type { MarketEvent } from "../types";

export function escapeHtml(value: unknown): string {
  return String(value ?? "").replace(
    /[&<>"]/g,
    (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
    })[character] ?? character,
  );
}

export function formatTime(iso: string): string {
  return /T(\d{2}:\d{2})/.exec(iso)?.[1] ?? "";
}

export function formatPercentage(value: number | null): string {
  return value == null
    ? "—"
    : `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;
}

const STATUS_LABELS = {
  official_confirmed: "官方確認",
  developing: "發展中",
  market_reacting: "市場反應中",
  preliminary: "初步消息",
} as const;

const SCORE_LOGIC = "以事件預期方向為基準，法人買賣超最高影響 ±25 分、股價表現最高影響 ±20 分，成交量作為反應強度係數；法人與股價方向分歧時，分數限制在 40–55 分。0–44 分：市場持相反意見；45–59 分：市場反應普通；60–100 分：市場反應一致。";

// 分數狀態機：observing/preliminary＝證據還在累積（前向報酬未全部到期），
// 每個盤後會用最新價量與法人資料重算，直到第 5 個交易日定案（verified）。
const PENDING_NOTE = "此分數尚未定案：事件後的股價報酬與法人動向仍在累積，每個盤後會重新計算，約第 5 個交易日後定案。";
const NEUTRAL_EVENT_LOGIC = "事件本身沒有明確的利多或利空方向，因此不計算市場反應一致性分數；股價、成交量與三大法人資料仍會如實呈現，供你查看事件發生後的市場變化。";
// 無分數的另外兩種「永遠不會有」：誠實標示，不掛著「觀察中」誤導使用者一直等
const NO_TICKER_LOGIC = "此事件未關聯特定個股（總經、政策或產業層級的消息），沒有可對應的股價與籌碼資料，因此不適用市場驗證分數。";
const NON_COMMON_STOCK_LOGIC = "此事件的主要標的不是上市櫃普通股（例如 ETF 或興櫃股票），資料來源不提供其三大法人買賣超，因此無法計算市場驗證分數。";
const UNSCORED_LOGIC = "此事件的方向判讀信心不足（多為傳聞或未經證實的消息），不以其方向計算市場反應一致性分數；股價、成交量與三大法人資料仍會如實呈現，供你自行判讀。";
// 與後端 TICKER_RE 同義：上市櫃普通股＝四碼、首碼非 0
const COMMON_STOCK_RE = /^[1-9]\d{3}$/;

export const SOURCE_TYPE_LABELS = {
  official: "官方",
  media: "媒體",
  gov: "公告",
} as const;

export function getStatusLabel(event: MarketEvent): string {
  return STATUS_LABELS[event.status] ?? "";
}

export function getMarketReactionLabel(score: number): string {
  if (score < 45) {
    return "市場持相反意見";
  }
  if (score < 60) {
    return "市場反應普通";
  }
  return "市場反應一致";
}

export function getFirstTicker(event: MarketEvent): MarketEvent["related_tickers"][number] | undefined {
  return event.related_tickers[0];
}

/** 重要性星等 ★★★★☆。長期只拿來排序、從未上畫面——簡報講「★1–5 可拆解」
 *  但 demo 看不到星星（2026-08-11 發現）。滿星實心、缺星空心，讀屏報數字。 */
export function renderStars(stars: number): string {
  const n = Math.max(0, Math.min(5, Math.round(stars)));
  return `<span class="stars" role="img" aria-label="重要性 ${n} 星（滿分 5 星）">`
    + "★".repeat(n) + "☆".repeat(5 - n) + "</span>";
}

export function renderScore(event: MarketEvent): string {
  if (event.market_validation == null) {
    const watch = (label: string, aria: string, logic: string): string =>
      `<div class="score score--watch">
        <span class="label">市場反應</span>
        <span class="score__value"><b>${label}</b>
          <span class="score__help" tabindex="0" aria-label="${aria}">?
            <span class="score__tooltip" role="tooltip">${logic}</span>
          </span>
        </span>
      </div>`;
    if (event.expected_direction === "中性") {
      return watch("中性事件", "查看中性事件說明", NEUTRAL_EVENT_LOGIC);
    }
    // 「觀察中」只留給真的在等資料的；永遠等不到的兩種要誠實講
    const first = getFirstTicker(event)?.ticker;
    if (!first) {
      return watch("不適用", "查看為何不適用", NO_TICKER_LOGIC);
    }
    if (!COMMON_STOCK_RE.test(first)) {
      return watch("無籌碼資料", "查看為何無籌碼資料", NON_COMMON_STOCK_LOGIC);
    }
    // 已定案（verified）卻沒有分數＝後端刻意不評分（方向信心 low 的傳聞類，
    // 見 market_validation 設計）——資料不會再來了，不能繼續掛「觀察中」騙人等
    if (event.verify_state === "verified") {
      return watch("不評分", "查看為何不評分", UNSCORED_LOGIC);
    }
    return `<div class="score score--watch"><span class="label">市場反應</span><b>觀察中</b></div>`;
  }

  const label = getMarketReactionLabel(event.market_validation);
  const settled = event.verify_state === "verified";
  const pendingTag = settled ? "" : `<span class="score__pending">反應中</span>`;
  const tooltip = settled ? SCORE_LOGIC : `${PENDING_NOTE}\n\n${SCORE_LOGIC}`;
  return `<div class="score">
    <span class="label">${label}${pendingTag}</span>
    <span class="score__value"><b>${event.market_validation}</b><span class="unit">/100</span>
      <span class="score__help" tabindex="0" aria-label="查看市場驗證評分邏輯">?
        <span class="score__tooltip" role="tooltip">${tooltip}</span>
      </span>
    </span>
  </div>`;
}

export function renderWatchButton(event: MarketEvent, isWatched: boolean): string {
  return `<button class="mark" type="button" data-mark="${escapeHtml(event.event_id)}" aria-pressed="${isWatched}" aria-label="加入自選" title="加入自選">${isWatched ? "★" : "☆"}</button>`;
}

export function renderGhostImage(event: MarketEvent, className: string): string {
  return `<span class="${className}">${escapeHtml(getFirstTicker(event)?.ticker ?? "E")}</span>`;
}

export function renderClusterMedia(event: MarketEvent): string {
  if (!event.image?.url) {
    return `<div class="cluster__media cluster__media--empty">${renderGhostImage(event, "cluster__ghost")}</div>`;
  }

  const credit = event.image.credit
    ? `<span class="cluster__credit">${escapeHtml(event.image.credit)}</span>`
    : "";
  const fallback = escapeHtml(getFirstTicker(event)?.ticker ?? "E");
  return `<div class="cluster__media"><img src="${escapeHtml(event.image.url)}" alt="${escapeHtml(event.image.alt || "")}" loading="lazy" data-image-fallback="cluster" data-fallback-text="${fallback}">${credit}</div>`;
}

export function renderTags(event: MarketEvent): string {
  return `<div class="cluster__tags">${event.categories.slice(0, 3).map((category) => `<span class="tag">${escapeHtml(category)}</span>`).join("")}</div>`;
}

export function renderMetaLine(event: MarketEvent): string {
  return [
    getFirstTicker(event)?.ticker,
    event.categories.join(" / "),
    getStatusLabel(event),
    `${event.source_count || 0} 則報導`,
  ].filter(Boolean).join(" · ");
}

export function renderEventCluster(event: MarketEvent, index: number): string {
  const lead = index === 0;
  const clusterIndex = lead
    ? "01 · 本期焦點事件"
    : `${String(index + 1).padStart(2, "0")} · 事件集合`;

  return `<a class="cluster acrylic ${lead ? "cluster--lead" : ""}" href="#event-${escapeHtml(event.event_id)}">
      ${renderClusterMedia(event)}
      <div class="cluster__body">
        <div class="cluster__kicker"><span class="cluster__idx">${clusterIndex}</span>
          <span class="cluster__conv"><b>${event.source_count || 0}</b> 則報導 → <b>1</b> 事件</span></div>
        ${renderTags(event)}
        <h3 class="cluster__title">${escapeHtml(event.title)}</h3>
        ${lead ? `<p class="cluster__deck">${escapeHtml(event.deck || "")}</p>` : ""}
        <div class="cluster__foot"><span class="cluster__meta">${escapeHtml(renderMetaLine(event))}</span>${renderScore(event)}</div>
      </div></a>`;
}

export function mountCardImageFallbacks(root: HTMLElement): () => void {
  const handleError = (event: Event): void => {
    const image = event.target;
    if (!(image instanceof HTMLImageElement)) {
      return;
    }

    const fallbackKind = image.dataset.imageFallback;
    const parent = image.parentElement;
    if (!fallbackKind || !parent) {
      return;
    }

    const fallback = escapeHtml(image.dataset.fallbackText ?? "E");
    if (fallbackKind === "cluster") {
      parent.classList.add("cluster__media--empty");
      parent.innerHTML = `<span class="cluster__ghost">${fallback}</span>`;
    }
  };

  root.addEventListener("error", handleError, true);
  return () => root.removeEventListener("error", handleError, true);
}
