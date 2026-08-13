import type {
  EventEditionMeta,
  MarketEvent,
} from "../types";
import type { Watchlist } from "../services/watchlist";
import {
  escapeHtml,
  formatTime,
  renderEventCluster,
  renderEventRow,
} from "../components";

import { mountEventStream, renderCategoryChips } from "./events";

export interface HomeRenderOptions {
  readonly events: readonly MarketEvent[];
  readonly meta: EventEditionMeta;
  readonly categories: readonly string[];
  readonly watchlist: Watchlist;
}

export function renderHome(options: HomeRenderOptions): string {
  const { events, meta, categories, watchlist } = options;
  const todayEvents = events.filter((event) => event.date === meta.edition_date);
  const editionEvents = todayEvents.length > 0 ? todayEvents : events;
  // 「本期」＝最新刊行日（edition_date），不假裝是「今日」——週末/連假時最新
  // 事件停在上一個交易日，掛上收盤日標籤誠實標示（如「7/31 收盤版」）。
  const editionDay = meta.edition_date
    ? `${Number(meta.edition_date.slice(5, 7))}/${Number(meta.edition_date.slice(8, 10))}`
    : "";
  const editionLabel = editionDay ? `${editionDay} 收盤版・` : "";
  // 首屏產業佔比條：本期報導集中在哪些產業（以報導篇數計，事件數為輔）。
  // industries 是選填欄（舊資料源沒有）——彙整不出東西就整塊不渲染，不擺空圖。
  const industryHeat = new Map<string, { articles: number; events: number }>();
  for (const event of editionEvents) {
    for (const industry of event.industries ?? []) {
      const bucket = industryHeat.get(industry) ?? { articles: 0, events: 0 };
      bucket.articles += Math.max(event.sources.length, 1);
      bucket.events += 1;
      industryHeat.set(industry, bucket);
    }
  }
  const heatEntries = [...industryHeat.entries()].sort(
    (a, b) => b[1].articles - a[1].articles,
  );
  const heatTotal = heatEntries.reduce((sum, [, n]) => sum + n.articles, 0);
  const heatTail = heatEntries.slice(6);
  // 佔比條：讀者要看的是「本期報導集中在哪」，不是無分母的絕對篇數。
  // 前 6 名各佔一段，第 7 名以後併成「其他」，整條加起來剛好是 100%。
  // 標籤省掉「業」字尾（半導體業→半導體），tooltip 仍給全名
  const heatSlices = heatEntries.slice(0, 6).map(([industry, n], index) => ({
    name: industry.replace(/業$/, ""),
    share: (n.articles / heatTotal) * 100,
    tip: `${industry}・${n.articles} 篇報導・${n.events} 件事件`,
    tone: 100 - index * 15,
  }));
  if (heatTail.length > 0) {
    const articles = heatTail.reduce((sum, [, n]) => sum + n.articles, 0);
    const tailEvents = heatTail.reduce((sum, [, n]) => sum + n.events, 0);
    heatSlices.push({
      name: `其他 ${heatTail.length} 產業`,
      share: (articles / heatTotal) * 100,
      tip: `其他 ${heatTail.length} 個產業・${articles} 篇報導・${tailEvents} 件事件`,
      tone: 14,
    });
  }
  const formatShare = (share: number) =>
    `${share >= 10 ? Math.round(share) : share.toFixed(1)}%`;
  const heatSummary = heatSlices
    .map((slice) => `${slice.name} ${formatShare(slice.share)}`)
    .join("、");
  const heatHtml = heatSlices.length
    ? `<div class="hero__heat">
        <p class="hero__heat-head">INDUSTRY HEAT<span>${editionDay ? `${editionDay} ` : ""}產業新聞熱度</span></p>
        <div class="heat-bar" role="img" aria-label="產業新聞熱度：${escapeHtml(heatSummary)}">
        ${heatSlices.map((slice) => `<i class="heat-seg" style="width:${slice.share.toFixed(2)}%;--tone:${slice.tone}%" data-tip="${escapeHtml(slice.tip)}・佔 ${formatShare(slice.share)}"></i>`).join("")}
        </div>
        <ul class="heat-keys">
        ${heatSlices.map((slice, index) => `
          <li class="heat-key${index < 3 ? " heat-key--lead" : ""}" data-tip="${escapeHtml(slice.tip)}">
            <i style="--tone:${slice.tone}%"></i>${escapeHtml(slice.name)}<b>${formatShare(slice.share)}</b>
          </li>`).join("")}
        </ul>
      </div>`
    : "";
  return `
    <section class="hero">
      <div class="hero__inner">
        <p class="hero__eyebrow">WHY JUSTMARKET <em>From scattered headlines to verifiable market events.</em></p>
        <h1 class="hero__title">把分散新聞，<span class="accent">收斂</span>成<br>可驗證的市場事件。</h1>
        <p class="hero__sub">整併多來源、還原事件脈絡，再用已發生的價格與籌碼資料，確認市場是否真的反應。</p>
        <div class="hero__facts acrylic">
          <div class="hero__stat"><span><b>${meta.total_sources || 0}</b> 篇報導</span><span class="arrow">→</span><span><b>${meta.total_events || events.length}</b> 個可驗證事件</span><span>更新 ${escapeHtml(formatTime(meta.as_of))}</span></div>
          ${heatHtml}
        </div>
      </div>
      <div class="hero__scroll">往下捲，看${editionDay ? `${editionDay} 收盤` : "最新"}事件</div>
    </section>
    <section class="section" id="curated">
      <p class="section__eyebrow">CURATED EVENT CLUSTERS</p>
      <div class="section__head"><h2 class="section__title">精選事件集合</h2>
        <p class="section__note">每一格都不是一篇文章，而是由多個來源交叉收斂的一件市場事件。</p></div>
      <div class="clusters">${editionEvents.slice(0, 5).map(renderEventCluster).join("")}</div>
    </section>
    <section class="section" id="stream">
      <p class="section__eyebrow">THE FULL EDITION</p>
      <div class="section__head"><div><h2 class="section__title">本期全部事件</h2>
        <p class="section__note">${editionLabel}精選負責排序，完整列表負責讓你看見全貌。</p></div>
        <a class="section__link" href="#all">開啟完整事件流 →</a></div>
      ${renderCategoryChips(categories)}
      <div class="stream" data-stream>${editionEvents.map((event) => renderEventRow(event, watchlist.has(event.event_id))).join("")}</div>
      <button class="stream__more acrylic" data-more type="button"></button>
    </section>`;
}

export const mountHome = mountEventStream;
