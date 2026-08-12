import type { MarketEvent } from "../types";
import type { Watchlist } from "../services/watchlist";
import { escapeHtml, renderBreadcrumb, renderEventRow } from "../components";
import { mountRowNavigation, renderCategoryChips } from "./events";

/** 排序方式。相關性＝後端預設（重要性→驗證分→日期），另兩種在前端就地重排。 */
export type SearchSort = "relevance" | "date" | "score";

const SORT_LABELS: ReadonlyArray<readonly [SearchSort, string]> = [
  ["relevance", "相關性"],
  ["date", "日期"],
  ["score", "市場驗證分"],
];

/** 排序：驗證分可能是 null（中性事件或低信心，設計上不計分）——一律排在最後，
 *  否則 null 會被當成 0 或霸佔開頭，兩種都會誤導。 */
export function sortEvents(
  events: readonly MarketEvent[],
  sort: SearchSort,
): readonly MarketEvent[] {
  const list = [...events];
  if (sort === "date") {
    return list.sort((a, b) =>
      (b.date || "").localeCompare(a.date || "")
      || b.stars - a.stars);
  }
  if (sort === "score") {
    return list.sort((a, b) => {
      const av = a.market_validation;
      const bv = b.market_validation;
      if (av == null && bv == null) return b.stars - a.stars;
      if (av == null) return 1;
      if (bv == null) return -1;
      return bv - av || b.stars - a.stars;
    });
  }
  return list;                    // relevance：沿用後端排序，不動
}

function renderSortChips(active: SearchSort): string {
  return `<div class="chips" data-chips="sort" role="group" aria-label="排序方式">${
    SORT_LABELS.map(([value, label]) =>
      `<button class="chip" type="button" data-sort="${value}" aria-pressed="${value === active}">${label}</button>`,
    ).join("")
  }</div>`;
}

export interface SearchRenderOptions {
  readonly query: string;
  readonly events: readonly MarketEvent[];
  readonly categories: readonly string[];
  readonly watchlist: Watchlist;
  readonly sort?: SearchSort;
  /** 查詢中（等後端回應）時先畫骨架，避免閃現「查無結果」 */
  readonly loading?: boolean;
}

export function renderSearchPage(options: SearchRenderOptions): string {
  const { query, events, categories, watchlist, loading } = options;
  const sort = options.sort ?? "relevance";
  const crumbs = renderBreadcrumb([{ t: "本期", href: "#home" }, { t: "搜尋結果" }]);
  const heading = `搜尋「${escapeHtml(query)}」`;

  if (loading) {
    return `<div class="page-wrap">${crumbs}
      <section class="section section--top">
        <p class="section__eyebrow">SEARCH</p>
        <div class="section__head"><h1 class="section__title">${heading}</h1></div>
        <p class="section__note">查詢中⋯</p>
      </section></div>`;
  }

  if (!events.length) {
    return `<div class="page-wrap">${crumbs}
      <section class="section section--top">
        <p class="section__eyebrow">SEARCH</p>
        <div class="section__head"><h1 class="section__title">${heading}</h1></div>
        <div class="empty acrylic">
          <b>找不到相符的事件</b>
          <p>可以試試公司名稱（台積電）、股票代號（2330），或事件關鍵字（法說、財報、增資）。</p>
          <a class="btn" href="#all">瀏覽完整事件流 →</a>
        </div>
      </section></div>`;
  }

  const sorted = sortEvents(events, sort);
  return `<div class="page-wrap">${crumbs}
      <section class="section section--top">
        <p class="section__eyebrow">SEARCH</p>
        <div class="section__head">
          <h1 class="section__title">${heading}</h1>
          <span class="big-count"><b>${events.length}</b> 件相符</span>
        </div>
        <p class="section__note">比對事件標題、摘要與相關個股。</p>
        ${renderSortChips(sort)}${renderCategoryChips(categories)}
        <div class="stream" data-stream>${
          sorted.map((event) => renderEventRow(event, watchlist.has(event.event_id))).join("")
        }</div>
      </section></div>`;
}

export interface MountSearchOptions {
  readonly events: readonly MarketEvent[];
  readonly watchlist: Watchlist;
  /** 排序改變時重畫串流；由 app.ts 提供，避免本模組直接碰路由。 */
  readonly onSortChange: (sort: SearchSort) => void;
}

export function mountSearchPage(root: HTMLElement, options?: MountSearchOptions): () => void {
  const stream = root.querySelector<HTMLElement>("[data-stream]");
  const disposeRowNav = stream ? mountRowNavigation(stream) : () => undefined;
  const sortGroup = root.querySelector<HTMLElement>('[data-chips="sort"]');
  const catGroup = root.querySelector<HTMLElement>('[data-chips="cat"]');

  const handleSort = (event: Event): void => {
    const button = (event.target as Element | null)?.closest<HTMLButtonElement>(".chip");
    if (!button || !sortGroup) {
      return;
    }
    sortGroup.querySelectorAll(".chip").forEach((chip) => {
      chip.setAttribute("aria-pressed", String(chip === button));
    });
    options?.onSortChange((button.dataset.sort ?? "relevance") as SearchSort);
  };

  // 分類篩選：就地顯示/隱藏，不重畫（重畫會讓排序狀態難以維持）
  const handleCategory = (event: Event): void => {
    const button = (event.target as Element | null)?.closest<HTMLButtonElement>(".chip");
    if (!button || !catGroup || !stream) {
      return;
    }
    catGroup.querySelectorAll(".chip").forEach((chip) => {
      chip.setAttribute("aria-pressed", String(chip === button));
    });
    const category = button.dataset.cat ?? "全部";
    Array.from(stream.children).forEach((row) => {
      if (!(row instanceof HTMLElement)) {
        return;
      }
      const cats = (row.dataset.cats ?? "").split("|");
      row.style.display = category === "全部" || cats.includes(category) ? "" : "none";
    });
  };

  sortGroup?.addEventListener("click", handleSort);
  catGroup?.addEventListener("click", handleCategory);
  return () => {
    disposeRowNav();
    sortGroup?.removeEventListener("click", handleSort);
    catGroup?.removeEventListener("click", handleCategory);
  };
}
