import type { DashboardSource, EventSource } from "./data";
import type { Watchlist } from "./services/watchlist";
import {
  mountBoard,
  mountEventDetail,
  mountEventListPage,
  mountHome,
  mountMethod,
  mountSearchPage,
  mountWatchlist,
  renderBoard,
  renderEventDetail,
  renderEventListPage,
  renderHome,
  renderMethod,
  renderSearchPage,
  renderWatchlist,
} from "./pages";
import type { SearchSort } from "./pages";
import { mountCardImageFallbacks, mountRowImageFallbacks } from "./components";
import { getNavigationRoute, parseRoute } from "./router";
import type { Dispose, MarketEvent } from "./types";

export interface CreateEventSignalAppOptions {
  readonly page: HTMLElement;
  readonly eventSource: EventSource;
  readonly dashboardSource: DashboardSource;
  readonly watchlist: Watchlist;
}

function renderAppShell(): string {
  return `
    <header class="site-header acrylic">
      <div class="header-bar">
        <a class="brand" href="#home" aria-label="EventSignal 首頁"><span class="brand__mark">E</span><strong>EventSignal</strong></a>
        <nav class="primary-nav" aria-label="主要導覽">
          <a href="#home" data-nav="home">本期</a><a href="#board" data-nav="board">今日大局</a>
          <a href="#all" data-nav="all">全部事件</a>
          <a href="#watchlist" data-nav="watchlist">自選</a><a href="#method" data-nav="method">方法</a>
        </nav>
        <form class="header-search" role="search">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"></circle><path d="m16 16 4 4"></path></svg>
          <input type="search" placeholder="搜尋公司或事件" aria-label="搜尋公司或事件" />
        </form>
      </div>
    </header>
    <main id="view"></main>
    <footer class="site-footer acrylic">
      <p>EventSignal・把新聞收斂成事件</p><p>資料源：EventSignal API（取不到資料時顯示錯誤，不以模擬值替代）</p><p>描述已發生反應，不提供買賣建議</p>
    </footer>`;
}

function createToast(): {
  readonly show: (message: string) => void;
  readonly dispose: Dispose;
} {
  let timeout: ReturnType<typeof setTimeout> | undefined;

  return {
    show(message: string): void {
      let toast = document.getElementById("toast");
      if (!toast) {
        toast = document.createElement("div");
        toast.id = "toast";
        toast.className = "toast";
        document.body.appendChild(toast);
      }

      toast.textContent = message;
      toast.classList.add("show");
      if (timeout !== undefined) {
        clearTimeout(timeout);
      }
      timeout = setTimeout(() => toast?.classList.remove("show"), 1800);
    },
    dispose(): void {
      if (timeout !== undefined) {
        clearTimeout(timeout);
      }
    },
  };
}

export async function createEventSignalApp(
  options: CreateEventSignalAppOptions,
): Promise<Dispose> {
  const { page, eventSource, dashboardSource, watchlist } = options;
  const [catalog, dashboard] = await Promise.all([
    eventSource.load(),
    dashboardSource.loadToday(),
  ]);

  page.innerHTML = renderAppShell();
  const view = page.querySelector<HTMLElement>("#view");
  if (!view) {
    throw new Error("EventSignal app shell did not render #view.");
  }

  const searchForm = page.querySelector<HTMLFormElement>(".header-search");
  const searchInput = searchForm?.querySelector<HTMLInputElement>("input[type=search]");
  let searchToken = 0;   // 競態保護：舊查詢的回應不得覆蓋新查詢的結果
  let detailToken = 0;   // 同上，用於詳情頁的非同步補抓
  const preventSearch = (event: SubmitEvent): void => {
    event.preventDefault();
    const q = searchInput?.value.trim() ?? "";
    // 走 hash 路由而非直接渲染：重新整理、上一頁、分享網址都能保留搜尋條件
    location.hash = q ? `#search=${encodeURIComponent(q)}` : "#all";
  };
  searchForm?.addEventListener("submit", preventSearch);
  const disposeCardImages = mountCardImageFallbacks(view);
  const disposeRowImages = mountRowImageFallbacks(view);
  const toast = createToast();
  let disposePage: Dispose = () => undefined;

  const render = (): void => {
    disposePage();
    disposePage = () => undefined;

    const route = parseRoute(location.hash);
    const navigationRoute = getNavigationRoute(route);
    page.querySelectorAll<HTMLAnchorElement>(".primary-nav a").forEach((anchor) => {
      anchor.setAttribute("aria-current", String(anchor.dataset.nav === navigationRoute));
    });
    document.body.classList.toggle("is-home", route.name === "home");
    document.body.classList.toggle("is-board", route.name === "board");

    if (route.name === "home") {
      view.innerHTML = renderHome({
        events: catalog.events,
        meta: catalog.meta,
        categories: catalog.categories,
        watchlist,
      });
      disposePage = mountHome(view);
    } else if (route.name === "board") {
      view.innerHTML = renderBoard(dashboard);
      disposePage = mountBoard(view, dashboard);
    } else if (route.name === "all") {
      // 日期選單資料：API 給的 available_dates；舊資料源沒有就從已載事件推導
      const availableDates = catalog.meta.available_dates
        ?? [...new Set(catalog.events.map((e) => e.date).filter(Boolean))].sort().reverse();
      const renderAllPage = (events: readonly MarketEvent[], activeDate: string): void => {
        const categories = ["全部", ...new Set(events.flatMap((e) => e.categories ?? []))];
        view.innerHTML = renderEventListPage({
          events, categories, watchlist, availableDates, activeDate,
        });
        disposePage = mountEventListPage(view);
        const select = view.querySelector<HTMLSelectElement>("[data-date-select]");
        select?.addEventListener("change", async () => {
          const value = select.value;
          select.disabled = true;
          let events2: readonly MarketEvent[] = catalog.events;
          if (value !== "all") {
            // 已載入的日期直接用；沒載過的向後端按日取（來源沒實作就只能顯示空）
            events2 = catalog.events.filter((e) => e.date === value);
            if (events2.length === 0 && eventSource.eventsByDay) {
              events2 = await eventSource.eventsByDay(value);
            }
          }
          disposePage();
          renderAllPage(events2, value);
        });
      };
      renderAllPage(catalog.events, "all");
    } else if (route.name === "watchlist") {
      view.innerHTML = renderWatchlist({ events: catalog.events, watchlist });
      disposePage = mountWatchlist(view);
    } else if (route.name === "method") {
      view.innerHTML = renderMethod();
      disposePage = mountMethod();
    } else if (route.name === "search") {
      const { q } = route;
      // 先畫載入骨架，再非同步填結果——否則會先閃一下「查無結果」
      view.innerHTML = renderSearchPage({
        query: q, events: [], categories: [], watchlist, loading: true,
      });
      const token = ++searchToken;   // 連續搜尋時，只有最後一次的結果算數
      void eventSource.search(q).then((result) => {
        if (token !== searchToken || parseRoute(location.hash).name !== "search") {
          return;   // 使用者已改搜別的字或離開此頁
        }
        // 排序改變時只重畫本頁，不重打 API——結果已在手上（上限 50 筆）
        const paint = (sort: SearchSort): void => {
          disposePage();
          view.innerHTML = renderSearchPage({
            query: q,
            events: result.events,
            categories: result.categories,
            watchlist,
            sort,
          });
          disposePage = mountSearchPage(view, {
            events: result.events, watchlist, onSortChange: paint,
          });
          window.scrollTo(0, 0);
        };
        paint("relevance");
      });
    } else {
      const { id } = route;
      const local = catalog.events.find((event) => event.event_id === id);
      view.innerHTML = renderEventDetail({ event: local, watchlist });
      disposePage = mountEventDetail();
      if (!local) {
        // 目錄只有 bootstrap 那批（預設 12 筆），從搜尋點進來、或直接開分享網址時
        // 都找不到 → 向後端補要。找不到才會落到詳情頁自己的 404 畫面。
        const token = ++detailToken;
        void eventSource.getEvent(id).then((fetched) => {
          const current = parseRoute(location.hash);
          if (!fetched || token !== detailToken
              || current.name !== "detail" || current.id !== id) {
            return;
          }
          disposePage();
          view.innerHTML = renderEventDetail({ event: fetched, watchlist });
          disposePage = mountEventDetail();
        });
      }
    }

    window.scrollTo(0, 0);
  };

  const handleViewClick = (event: MouseEvent): void => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }

    const mark = target.closest<HTMLElement>("[data-mark]");
    const eventId = mark?.dataset.mark;
    if (mark && eventId) {
      const isWatched = watchlist.toggle(eventId);
      mark.setAttribute("aria-pressed", String(isWatched));
      mark.textContent = mark.classList.contains("btn--mark")
        ? isWatched ? "★ 已加入自選" : "☆ 加入自選"
        : isWatched ? "★" : "☆";
      if (parseRoute(location.hash).name === "watchlist") {
        render();
      }
      return;
    }

    if (!target.closest("[data-share]")) {
      return;
    }

    const url = location.href;
    if (navigator.clipboard) {
      void navigator.clipboard.writeText(url).then(
        () => toast.show("已複製連結"),
        () => toast.show(url),
      );
    } else {
      toast.show(url);
    }
  };

  const handleHashChange = (): void => render();
  view.addEventListener("click", handleViewClick);
  window.addEventListener("hashchange", handleHashChange);
  render();

  return () => {
    window.removeEventListener("hashchange", handleHashChange);
    view.removeEventListener("click", handleViewClick);
    searchForm?.removeEventListener("submit", preventSearch);
    disposePage();
    disposeCardImages();
    disposeRowImages();
    toast.dispose();
  };
}

export type { EventSource } from "./data";
export type { DashboardSource } from "./data";
export type { Watchlist } from "./services/watchlist";
export type { Dispose } from "./types";
