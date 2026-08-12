import type { MarketEvent } from "../types";
import type { Watchlist } from "../services/watchlist";
import { renderBreadcrumb, renderEventRow } from "../components";
import { mountEventStream } from "./events";

export interface WatchlistRenderOptions {
  readonly events: readonly MarketEvent[];
  readonly watchlist: Watchlist;
}

export function renderWatchlist(options: WatchlistRenderOptions): string {
  const { events, watchlist } = options;
  const watchedIds = watchlist.list();
  const watchedEvents = events.filter((event) => watchedIds.includes(event.event_id));
  const body = watchedEvents.length
    ? `<div class="stream" data-stream>${watchedEvents.map((event) => renderEventRow(event, true)).join("")}</div>`
    : `<div class="empty acrylic"><b>自選是空的</b><p>在任何事件列表點 ☆，或到事件詳情按「加入自選」，就會出現在這裡。</p><a class="btn" href="#all">去看全部事件 →</a></div>`;

  return `<div class="page-wrap">
      ${renderBreadcrumb([{ t: "本期", href: "#home" }, { t: "自選" }])}
      <section class="section section--top">
        <p class="section__eyebrow">YOUR WATCHLIST</p>
        <div class="section__head"><h1 class="section__title">自選</h1><span class="big-count"><b>${watchedEvents.length}</b> 件收藏</span></div>
        <p class="section__note">只追你在意的事件；事件內容取自 EventSignal API，收藏存在本機瀏覽器。</p>
        ${body}
      </section></div>`;
}

export const mountWatchlist = mountEventStream;
