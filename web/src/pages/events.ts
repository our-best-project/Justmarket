import type { MarketEvent } from "../types";
import type { Watchlist } from "../services/watchlist";
import {
  escapeHtml,
  renderBreadcrumb,
  renderEventRow,
} from "../components";

const STREAM_INITIAL_COUNT = 6;

function previousIsoDate(value: string): string {
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  date.setUTCDate(date.getUTCDate() - 1);
  return date.toISOString().slice(0, 10);
}

export interface EventListRenderOptions {
  readonly events: readonly MarketEvent[];
  readonly categories: readonly string[];
  readonly watchlist: Watchlist;
  /** 有事件的日期（新→舊）；空陣列時選單只剩「近兩日」 */
  readonly availableDates?: readonly string[];
  /** 目前選取："all"（近兩日）或 ISO 日期 */
  readonly activeDate?: string;
}

export function renderCategoryChips(categories: readonly string[], kind = "cat"): string {
  return `<div class="chips" data-chips="${kind}">${categories.map((category, index) => `<button class="chip" type="button" data-cat="${escapeHtml(category)}" aria-pressed="${index === 0}">${escapeHtml(category)}</button>`).join("")}</div>`;
}

/** 日期選單：取代舊的 今天/昨天/全部 chips——可以跳到任何一個有事件的收盤日 */
export function renderDateSelect(availableDates: readonly string[], activeDate: string): string {
  const label = (iso: string): string =>
    `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;
  const options = [
    `<option value="all"${activeDate === "all" ? " selected" : ""}>近兩日</option>`,
    ...availableDates.map((d) =>
      `<option value="${escapeHtml(d)}"${d === activeDate ? " selected" : ""}>${label(d)}</option>`),
  ].join("");
  return `<label class="date-select">日期<select data-date-select aria-label="選擇收盤日">${options}</select></label>`;
}

export function renderEventListPage(options: EventListRenderOptions): string {
  const { events, categories, watchlist, availableDates = [], activeDate = "all" } = options;
  const streamHtml = events.length
    ? `<div class="stream" data-stream>${events.map((event) => renderEventRow(event, watchlist.has(event.event_id))).join("")}</div>`
    : `<div class="empty acrylic"><b>這一天沒有已評分事件</b><p>可能是週末或假日；換一個日期看看。</p></div>`;
  return `<div class="page-wrap">
      ${renderBreadcrumb([{ t: "本期", href: "#home" }, { t: "全部事件" }])}
      <section class="section section--top">
        <p class="section__eyebrow">ALL EVENTS</p>
        <div class="section__head"><h1 class="section__title">完整事件流</h1>${renderDateSelect(availableDates, activeDate)}</div>
        <p class="section__note">不是新聞瀑布；每一列都是去重後的一件事。</p>
        ${renderCategoryChips(categories)}
        ${streamHtml}
      </section></div>`;
}

/** 整塊卡片可點：委派處理，但三種情況要放行，否則會妨礙正常操作。 */
export function mountRowNavigation(stream: HTMLElement): () => void {
  const onClick = (event: MouseEvent): void => {
    if (event.defaultPrevented || event.button !== 0
        || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;                       // ① 修飾鍵／中鍵：交給瀏覽器開新分頁
    }
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    if (target.closest("a, button, input, [data-mark], .score__help")) {
      return;                       // ② 卡片內本來就可操作的元素，各自處理
    }
    if ((window.getSelection()?.toString() ?? "").length > 0) {
      return;                       // ③ 使用者正在選取文字——這正是要能選取的重點
    }
    const row = target.closest<HTMLElement>(".row[data-event-id]");
    const id = row?.dataset.eventId;
    if (id) {
      location.hash = `#event-${id}`;
    }
  };
  stream.addEventListener("click", onClick);
  return () => stream.removeEventListener("click", onClick);
}

export function mountEventStream(root: HTMLElement): () => void {
  const stream = root.querySelector<HTMLElement>("[data-stream]");
  if (!stream) {
    return () => undefined;
  }
  const disposeRowNav = mountRowNavigation(stream);

  const rows = Array.from(stream.children).filter((row): row is HTMLElement => row instanceof HTMLElement);
  const today = rows
    .map((row) => row.dataset.date ?? "")
    .filter(Boolean)
    .sort()
    .at(-1) ?? "";
  const yesterday = previousIsoDate(today);
  const moreButton = root.querySelector<HTMLButtonElement>("[data-more]");
  const chipGroups = Array.from(root.querySelectorAll<HTMLElement>("[data-chips]"));
  let category = "全部";
  let date = root.querySelector('[data-chips="date"]') ? "today" : "all";
  let expanded = false;

  const apply = (): void => {
    const matched = rows.filter((row) => {
      const matchesCategory = category === "全部"
        || (row.dataset.cats ?? "").split("|").includes(category);
      const matchesDate = date === "all"
        || (date === "today" && row.dataset.date === today)
        || (date === "yesterday" && row.dataset.date === yesterday);
      return matchesCategory && matchesDate;
    });

    rows.forEach((row) => {
      row.style.display = "none";
    });
    const limit = moreButton && !expanded
      ? Math.min(STREAM_INITIAL_COUNT, matched.length)
      : matched.length;
    matched.slice(0, limit).forEach((row) => {
      row.style.display = "";
    });

    if (!moreButton) {
      return;
    }

    const remaining = matched.length - limit;
    if (remaining > 0) {
      moreButton.style.display = "";
      moreButton.textContent = `查看其餘 ${remaining} 件事件 →`;
    } else if (expanded && matched.length > STREAM_INITIAL_COUNT) {
      moreButton.style.display = "";
      moreButton.textContent = "收合";
    } else {
      moreButton.style.display = "none";
    }
  };

  const handleMore = (): void => {
    expanded = !expanded;
    apply();
  };
  const handleChipClick = (event: Event): void => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }

    const button = target.closest<HTMLButtonElement>(".chip");
    const group = event.currentTarget;
    if (!button || !(group instanceof HTMLElement)) {
      return;
    }

    if (group.dataset.chips === "date") {
      date = button.dataset.date ?? "";
    } else {
      category = button.dataset.cat ?? "";
    }
    expanded = false;
    group.querySelectorAll(".chip").forEach((chip) => {
      chip.setAttribute("aria-pressed", String(chip === button));
    });
    apply();
  };

  moreButton?.addEventListener("click", handleMore);
  chipGroups.forEach((group) => group.addEventListener("click", handleChipClick));
  apply();

  return () => {
    disposeRowNav();
    moreButton?.removeEventListener("click", handleMore);
    chipGroups.forEach((group) => group.removeEventListener("click", handleChipClick));
  };
}

export const mountEventListPage = mountEventStream;
