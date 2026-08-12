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
  // 首屏左下產業熱度表：本期哪些產業新聞最多（以報導篇數計，事件數為輔）。
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
  const heatRows = [...industryHeat.entries()]
    .sort((a, b) => b[1].articles - a[1].articles)
    .slice(0, 6);
  const heatMax = heatRows[0]?.[1].articles ?? 0;
  const heatHtml = heatRows.length
    ? `<div class="hero__heat acrylic" aria-label="產業新聞熱度">
        <p class="hero__heat-head">INDUSTRY HEAT<span>${editionDay ? `${editionDay} ` : ""}產業新聞熱度・以報導篇數計</span></p>
        <ul class="heat-list">
        ${heatRows.map(([industry, n]) => `
          <li class="heat-row">
            <span class="heat-row__name" title="${escapeHtml(industry)}">${escapeHtml(industry)}</span>
            <span class="heat-row__bar"><i style="width:${Math.max(Math.round((n.articles / heatMax) * 100), 4)}%"></i></span>
            <span class="heat-row__count"><b>${n.articles}</b> 篇・${n.events} 件</span>
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
        <div class="hero__stat acrylic"><span><b>${meta.total_sources || 0}</b> 篇報導</span><span class="arrow">→</span><span><b>${meta.total_events || events.length}</b> 個可驗證事件</span><span>更新 ${escapeHtml(formatTime(meta.as_of))}</span></div>
      </div>
      ${heatHtml}
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
