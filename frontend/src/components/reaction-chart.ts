import type { MarketReaction, MarketReactionPoint } from "../types";
import { escapeHtml } from "./event-card";

type InstitutionalKey = "foreign_net" | "trust_net" | "dealer_net";

const INSTITUTIONAL_ROWS: readonly {
  readonly key: InstitutionalKey;
  readonly label: string;
}[] = [
  { key: "foreign_net", label: "外資" },
  { key: "trust_net", label: "投信" },
  { key: "dealer_net", label: "自營商" },
];

function formatDate(value: string): string {
  const isoDate = /^\d{4}-(\d{2})-(\d{2})$/.exec(value);
  return isoDate ? `${isoDate[1]}/${isoDate[2]}` : value;
}

function formatPrice(value: number): string {
  const digits = value >= 1000 ? 0 : value >= 100 ? 1 : 2;
  return value.toLocaleString("zh-TW", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatLots(shares: number): string {
  if (shares === 0) return "0";

  const lots = Math.abs(shares) / 1000;
  const sign = shares > 0 ? "+" : "−";
  if (lots >= 10_000) return `${sign}${(lots / 10_000).toFixed(1)}萬`;
  if (lots >= 1000) return `${sign}${(lots / 1000).toFixed(1)}k`;
  if (lots >= 10) return `${sign}${Math.round(lots).toLocaleString("zh-TW")}`;
  return `${sign}${lots.toFixed(1)}`;
}

function renderInstitutionalFlow(
  series: readonly MarketReactionPoint[],
  eventIndex: number,
): string {
  const maximum = Math.max(
    ...series.flatMap((point) =>
      INSTITUTIONAL_ROWS.map(({ key }) => Math.abs(point[key])),
    ),
    1,
  );

  const dates = series
    .map(
      (point, index) =>
        `<span class="inst-date ${index === eventIndex ? "inst-date--event" : ""}" title="${escapeHtml(point.date)}">${escapeHtml(formatDate(point.date))}</span>`,
    )
    .join("");

  const rows = INSTITUTIONAL_ROWS.map(({ key, label }) => {
    const cells = series
      .map((point, index) => {
        const value = point[key];
        const state = value > 0 ? "buy" : value < 0 ? "sell" : "flat";
        const strength = value === 0
          ? 0
          : 0.16 + (Math.abs(value) / maximum) * 0.64;
        const action = value > 0 ? "買超" : value < 0 ? "賣超" : "無買賣超";
        const fullValue = value === 0
          ? "0 張"
          : `${(Math.abs(value) / 1000).toLocaleString("zh-TW", {
              maximumFractionDigits: 3,
            })} 張`;
        return `<span
          class="inst-cell inst-cell--${state} ${index === eventIndex ? "inst-cell--event" : ""}"
          style="--flow-alpha:${strength.toFixed(2)}"
          title="${escapeHtml(point.date)} ${label}${action} ${fullValue}"
          aria-label="${escapeHtml(point.date)} ${label}${action} ${fullValue}">
          <b>${formatLots(value)}</b>
        </span>`;
      })
      .join("");

    return `<div class="inst-row">
      <span class="inst-row__label">${label}</span>
      ${cells}
    </div>`;
  }).join("");

  return `<div class="institutional-flow">
    <div class="institutional-flow__head">
      <div>
        <span class="institutional-flow__kicker">INSTITUTIONAL FLOW</span>
        <strong>三大法人每日買賣超</strong>
      </div>
      <div class="institutional-flow__legend" aria-label="買賣超圖例">
        <span class="institutional-flow__legend-buy">買超</span>
        <span class="institutional-flow__legend-sell">賣超</span>
        <span>單位：張</span>
      </div>
    </div>
    <div class="institutional-flow__scroll">
      <div class="institutional-flow__grid" style="--flow-columns:${series.length}">
        <div class="inst-dates"><span></span>${dates}</div>
        ${rows}
      </div>
    </div>
  </div>`;
}

export function renderMarketReactionChart(reaction: MarketReaction): string {
  const series = reaction.series;
  if (!series.length) {
    return "";
  }

  const width = 760;
  const height = 292;
  const left = 68;
  const right = 20;
  const top = 32;
  const bottom = 238;
  const closes = series.map((point) => point.close);
  const rawMinimum = Math.min(...closes);
  const rawMaximum = Math.max(...closes);
  const rawSpan = rawMaximum - rawMinimum;
  const padding = Math.max(rawSpan * 0.12, rawMaximum * 0.006, 0.5);
  const minimum = Math.max(0, rawMinimum - padding);
  const maximum = rawMaximum + padding;
  const span = Math.max(maximum - minimum, 1);
  const denominator = Math.max(series.length - 1, 1);
  const x = (index: number): number =>
    left + index * ((width - left - right) / denominator);
  const y = (value: number): number =>
    top + (bottom - top) * (1 - (value - minimum) / span);
  const eventIndex = Math.min(
    Math.max(reaction.event_index ?? Math.floor(series.length / 2), 0),
    series.length - 1,
  );
  const line = closes
    .map(
      (value, index) =>
        `${index ? "L" : "M"}${x(index).toFixed(1)} ${y(value).toFixed(1)}`,
    )
    .join(" ");
  const area = `${line} L${x(series.length - 1).toFixed(1)} ${bottom} L${x(0).toFixed(1)} ${bottom} Z`;
  const ticks = Array.from(
    { length: 5 },
    (_, index) => maximum - (span / 4) * index,
  );
  const grid = ticks
    .map((tick) => {
      const tickY = y(tick);
      return `<line x1="${left}" y1="${tickY.toFixed(1)}" x2="${width - right}" y2="${tickY.toFixed(1)}" class="ct-grid"/>
        <text x="${left - 12}" y="${(tickY + 4).toFixed(1)}" class="ct-y">${formatPrice(tick)}</text>`;
    })
    .join("");
  const labels = series
    .map(
      (point, index) =>
        `<text x="${x(index).toFixed(1)}" y="${bottom + 24}" class="ct-x">${escapeHtml(formatDate(point.date))}</text>`,
    )
    .join("");
  const points = series
    .map(
      (point, index) =>
        `<circle cx="${x(index).toFixed(1)}" cy="${y(point.close).toFixed(1)}" r="${index === eventIndex ? "4.2" : "2.5"}" class="${index === eventIndex ? "ct-dot ct-dot--event" : "ct-dot"}">
          <title>${escapeHtml(point.date)} 收盤 ${formatPrice(point.close)} 元</title>
        </circle>`,
    )
    .join("");

  return `<div class="reaction-visual">
    <div class="reaction-chart-scroll">
      <svg class="reaction-chart" viewBox="0 0 ${width} ${height}" role="img"
        aria-label="${escapeHtml(reaction.ticker)} 事件前後每日收盤價折線圖，X 軸為日期，Y 軸為股價">
        <defs>
          <linearGradient id="reaction-price-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stop-color="var(--ember)" stop-opacity=".24"/>
            <stop offset="1" stop-color="var(--ember)" stop-opacity="0"/>
          </linearGradient>
        </defs>
        <text x="${left}" y="17" class="ct-caption">${escapeHtml(reaction.ticker)}・每日收盤價</text>
        <text x="16" y="${(top + bottom) / 2}" class="ct-axis-title" transform="rotate(-90 16 ${(top + bottom) / 2})">股價（元）</text>
        ${grid}
        <path d="${area}" fill="url(#reaction-price-fill)"/>
        <line x1="${x(eventIndex).toFixed(1)}" y1="${top}" x2="${x(eventIndex).toFixed(1)}" y2="${bottom}" class="ct-ev"/>
        <text x="${x(eventIndex).toFixed(1)}" y="${top - 8}" class="ct-evlabel">事件日</text>
        <path d="${line}" class="ct-line"/>
        ${points}
        ${labels}
      </svg>
    </div>
    ${renderInstitutionalFlow(series, eventIndex)}
  </div>`;
}
