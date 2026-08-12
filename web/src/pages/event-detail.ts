import type { MarketEvent } from "../types";
import type { Watchlist } from "../services/watchlist";
import {
  renderStars,
  escapeHtml,
  getFirstTicker,
  getStatusLabel,
  renderBreadcrumb,
  renderGhostImage,
  renderMetaLine,
  renderMarketReactionChart,
  renderScore,
  renderTags,
  SOURCE_TYPE_LABELS,
} from "../components";

export interface EventDetailRenderOptions {
  readonly event: MarketEvent | undefined;
  readonly watchlist: Watchlist;
}

export function renderEventDetail(options: EventDetailRenderOptions): string {
  const { event, watchlist } = options;
  if (!event) {
    return `<div class="page-wrap">${renderBreadcrumb([{ t: "本期", href: "#home" }, { t: "事件詳情" }])}<div class="empty acrylic"><b>找不到這個事件</b><a class="btn" href="#all">回全部事件 →</a></div></div>`;
  }

  const reaction = event.market_reaction;
  const ticker = getFirstTicker(event);
  const media = event.image?.url
    ? `<div class="dt-hero__media"><img src="${escapeHtml(event.image.url)}" alt="${escapeHtml(event.image.alt || "")}">${event.image.credit ? `<span class="cluster__credit">${escapeHtml(event.image.credit)}</span>` : ""}</div>`
    : `<div class="dt-hero__media cluster__media--empty">${renderGhostImage(event, "cluster__ghost")}</div>`;
  const reactionCard = reaction
    ? `<div class="dt-card acrylic dt-reaction">
        <div class="dt-card__head"><p class="section__eyebrow">市場反應</p><span class="dt-asof">更新至 ${escapeHtml(reaction.as_of || "")}</span></div>
        <h2 class="dt-card__title">事件前後，股價與法人怎麼走？</h2>
        ${renderMarketReactionChart(reaction)}
        <p class="dt-fine">股價為每日收盤價；法人買賣超由股數換算為張。紅色為買超、綠色為賣超，事件日以暖色虛線標記。只描述已發生反應。</p>
      </div>`
    : "";
  const timeline = event.timeline.length
    ? `<div class="dt-card acrylic dt-timeline">
        <p class="section__eyebrow">TIMELINE</p><h2 class="dt-card__title">事件怎麼走到現在</h2>
        <ol class="tl">${event.timeline.map((item) => `<li class="${item.current ? "tl--now" : ""}"><span class="tl__date">${escapeHtml(item.date)}</span><span class="tl__title">${escapeHtml(item.title)}</span></li>`).join("")}</ol>
      </div>`
    : "";
  const sources = event.sources.length
    ? `<div class="dt-card acrylic dt-sources">
        <p class="section__eyebrow">FULL COVERAGE</p><h2 class="dt-card__title">${event.source_count || event.sources.length} 則來源，先看這 ${event.sources.length} 則</h2>
        <p class="section__note">事件被合併，來源標題仍完整保留。</p>
        <ol class="srcs">${event.sources.map((source, index) => `<li class="src"><span class="src__i">${String(index + 1).padStart(2, "0")}</span>
          <div class="src__body"><div class="src__top"><span class="src__badge src__badge--${escapeHtml(source.source_type)}">${SOURCE_TYPE_LABELS[source.source_type] || "來源"}</span><span class="src__name">${escapeHtml(source.source)}</span><span class="src__time">${escapeHtml((source.published_at || "").slice(5, 16).replace("T", " "))}</span></div>
          <p class="src__title">${escapeHtml(source.title)}</p></div>
          <a class="src__link" href="${escapeHtml(source.url || "#")}" target="_blank" rel="noopener">${source.has_unique_detail ? "含獨家細節" : "開啟來源"} ↗</a></li>`).join("")}</ol>
      </div>`
    : "";
  const isWatched = watchlist.has(event.event_id);

  return `<div class="page-wrap dt">
      ${renderBreadcrumb([{ t: "本期", href: "#home" }, { t: "全部事件", href: "#all" }, { t: "事件詳情" }])}
      <section class="dt-hero acrylic">
        ${media}
        <div class="dt-hero__body">
          <div class="dt-hero__top"><span class="dt-status">${getStatusLabel(event)}</span>${renderStars(event.stars)}<span class="dt-time">${escapeHtml(event.occurred_at_text || event.date || "")}</span></div>
          <h1 class="dt-title">${escapeHtml(event.title)}</h1>
          <p class="dt-deck">${escapeHtml(event.deck || "")}</p>
          ${renderTags(event)}
          <div class="dt-actions">
            <button class="btn btn--mark" data-mark="${escapeHtml(event.event_id)}" aria-pressed="${isWatched}">${isWatched ? "★ 已加入自選" : "☆ 加入自選"}${ticker ? ` ${escapeHtml(ticker.ticker)}` : ""}</button>
            <button class="btn" data-share type="button">分享 ↗</button>
          </div>
        </div>
      </section>
      <div class="dt-grid">
        <div class="dt-card acrylic dt-brief">
          <p class="section__eyebrow">EVENT BRIEF</p><h2 class="dt-card__title">一分鐘讀懂</h2>
          <p class="dt-summary">${escapeHtml(event.summary || event.deck || "")}</p>
          <div class="dt-brief__foot"><span class="cluster__meta">${escapeHtml(renderMetaLine(event))}</span>${renderScore(event)}</div>
        </div>
        <div class="dt-card acrylic dt-why">
          <p class="section__eyebrow">WHY IT MATTERS</p><h2 class="dt-card__title">為什麼值得看</h2>
          <ol class="why">${event.importance_reasons.map((reason, index) => `<li><span>${String(index + 1).padStart(2, "0")}</span>${escapeHtml(reason)}</li>`).join("")}</ol>
        </div>
        ${reactionCard}
        ${timeline}
        ${sources}
      </div>
    </div>`;
}

export function mountEventDetail(): () => void {
  return () => undefined;
}
