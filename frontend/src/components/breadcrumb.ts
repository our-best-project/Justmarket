import { escapeHtml } from "./event-card";

export interface BreadcrumbItem {
  readonly t: string;
  readonly href?: string;
}

export function renderBreadcrumb(items: readonly BreadcrumbItem[]): string {
  return `<nav class="crumb">${items.map((item) => item.href
    ? `<a href="${item.href}">${escapeHtml(item.t)}</a>`
    : `<span>${escapeHtml(item.t)}</span>`).join("<i>/</i>")}</nav>`;
}
