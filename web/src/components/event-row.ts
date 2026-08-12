import type { MarketEvent } from "../types";
import {
  escapeHtml,
  getFirstTicker,
  renderMetaLine,
  renderScore,
  renderStars,
  renderWatchButton,
} from "./event-card";

export function renderEventRow(event: MarketEvent, isWatched: boolean): string {
  const fallback = escapeHtml(getFirstTicker(event)?.ticker ?? "E");
  const thumbnail = event.image?.url
    ? `<div class="row__thumb"><img src="${escapeHtml(event.image.url)}" alt="" loading="lazy" data-image-fallback="row" data-fallback-text="${fallback}"></div>`
    : `<div class="row__thumb"><span>${fallback}</span></div>`;

  // 不用覆蓋式連結（絕對定位的透明 <a> 蓋住整張卡）——那會讓卡片內的文字
  // 完全無法用滑鼠選取。改成：標題是真連結（鍵盤、中鍵開新分頁、無障礙都正常），
  // 整塊的可點性由 mountEventStream 的委派處理，並在偵測到使用者正在選取文字時放行。
  return `<div class="row acrylic is-clickable" data-event-id="${escapeHtml(event.event_id)}" data-cats="${escapeHtml(event.categories.join("|"))}" data-date="${escapeHtml(event.date || "")}">
      ${thumbnail}
      <div class="row__body">
        <div class="row__top"><span>${escapeHtml(event.occurred_at_text || event.date || "")}</span>${renderStars(event.stars)}<span>${escapeHtml(event.categories.join(" / "))}</span></div>
        <h4 class="row__title"><a class="row__link" href="#event-${escapeHtml(event.event_id)}">${escapeHtml(event.title)}</a></h4>
        <p class="row__deck">${escapeHtml(event.deck || "")}</p>
        <span class="row__meta">${escapeHtml(renderMetaLine(event))}</span>
      </div>
      <div class="row__side">${renderScore(event)}${renderWatchButton(event, isWatched)}</div>
    </div>`;
}

export function mountRowImageFallbacks(root: HTMLElement): () => void {
  const handleError = (event: Event): void => {
    const image = event.target;
    if (
      !(image instanceof HTMLImageElement)
      || image.dataset.imageFallback !== "row"
      || !image.parentElement
    ) {
      return;
    }

    image.parentElement.innerHTML = `<span>${escapeHtml(image.dataset.fallbackText ?? "E")}</span>`;
  };

  root.addEventListener("error", handleError, true);
  return () => root.removeEventListener("error", handleError, true);
}
