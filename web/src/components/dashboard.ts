import type {
  DashboardSeriesPoint,
  GlobalMarket,
  IndustryBreadth,
  TaiwanBreadth,
  TurnoverBubble,
} from "../types";
import { escapeHtml } from "./event-card";
import { describeBreadthPanel } from "./market-narrative";

const signed = (value: number, digits = 2): string =>
  `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;

const direction = (value: number): "up" | "down" | "flat" =>
  value > 0 ? "up" : value < 0 ? "down" : "flat";

function sparklinePoints(
  series: readonly DashboardSeriesPoint[],
  width: number,
  height: number,
  inset = 6,
): string {
  const values = series.map((point) => point.value);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const range = Math.max(maximum - minimum, 0.001);
  return series
    .map((point, index) => {
      const x = inset + (index / Math.max(series.length - 1, 1)) * (width - inset * 2);
      const y = height - inset - ((point.value - minimum) / range) * (height - inset * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

export function renderGlobalPulse(markets: readonly GlobalMarket[]): string {
  const nodes = markets
    .map(
      (market, index) => `
        <button class="world-node world-node--${direction(market.change1d)}"
          type="button" data-market="${escapeHtml(market.id)}"
          aria-pressed="${String(index === 0)}"
          style="--map-x:${market.mapX}%;--map-y:${market.mapY}%"
          aria-label="${escapeHtml(market.name)} ${signed(market.change1d)}">
          <span class="world-node__pulse"></span>
          <span class="world-node__label"><b>${escapeHtml(market.index)}</b><em>${signed(market.change1d)}</em></span>
        </button>`,
    )
    .join("");

  const ranking = [...markets]
    .sort((left, right) => right.change1d - left.change1d)
    .map(
      (market, index) => `
        <button type="button" class="market-rank" data-market="${escapeHtml(market.id)}"
          aria-pressed="${String(market.id === markets[0]?.id)}">
          <span class="market-rank__n">${String(index + 1).padStart(2, "0")}</span>
          <span><b>${escapeHtml(market.name)}</b><small>${escapeHtml(market.index)} · ${escapeHtml(market.delay)}</small></span>
          <em class="${direction(market.change1d)}">${signed(market.change1d)}</em>
        </button>`,
    )
    .join("");

  return `
    <section class="dash-panel global-pulse" aria-labelledby="global-title">
      <div class="dash-panel__head">
        <div><p class="dash-kicker">GLOBAL PULSE / 01</p><h2 id="global-title">全球市場脈動</h2></div>
        <div class="dash-legend" aria-label="漲跌圖例"><span class="up">▲ 上漲</span><span class="down">▼ 下跌</span><span>● 無資料</span></div>
      </div>
      <div class="global-pulse__grid">
        <div class="world-stage">
          <div class="world-stage__halo" aria-hidden="true"></div>
          <svg class="world-map" viewBox="0 0 1000 470" aria-hidden="true">
            <path d="M83 127l93-53 112 27 42 55-25 57-72 18-36 64-60-28-8-59-55-33z"/>
            <path d="M264 292l49 12 34 58-24 91-39-22-17-75z"/>
            <path d="M420 121l72-31 52 26-11 41-65 19-34-18z"/>
            <path d="M505 178l72-25 89 11 54-34 133 34 85 67-28 44-108-2-54 37-76-13-62-59-75-4z"/>
            <path d="M526 239l76 5 48 83-31 98-70-37-37-88z"/>
            <path d="M807 340l82-27 61 44-20 55-91 2-42-40z"/>
          </svg>
          <div class="world-stage__latitudes" aria-hidden="true"></div>
          ${nodes}
          <p class="world-stage__note">節點代表主要指數，不代表整個國家</p>
        </div>
        <div class="global-pulse__side">
          <div class="market-detail" data-market-detail aria-live="polite"></div>
          <div class="market-ranking" aria-label="全球市場單日漲跌排名">${ranking}</div>
        </div>
      </div>
    </section>`;
}

export function renderMarketDetail(market: GlobalMarket): string {
  const first = market.series20[0]?.value ?? 100;
  const last = market.series20.at(-1)?.value ?? first;
  const lineDirection = direction(last - first);
  return `
    <div class="market-detail__top">
      <div><span>${escapeHtml(market.country)} · ${escapeHtml(market.index)}</span><h3>${escapeHtml(market.name)}</h3></div>
      <strong class="${direction(market.change1d)}">${signed(market.change1d)}</strong>
    </div>
    <svg class="market-spark market-spark--${lineDirection}" viewBox="0 0 420 116" role="img"
      aria-label="${escapeHtml(market.name)}二十個交易日標準化走勢">
      <defs><linearGradient id="market-fill-${escapeHtml(market.id)}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="currentColor" stop-opacity=".28"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/>
      </linearGradient></defs>
      <path class="market-spark__base" d="M6 58H414"/>
      <polygon points="6,110 ${sparklinePoints(market.series20, 420, 104)} 414,110"
        fill="url(#market-fill-${escapeHtml(market.id)})"/>
      <polyline points="${sparklinePoints(market.series20, 420, 104)}"/>
    </svg>
    <div class="market-detail__periods">
      <span><small>5D</small><b class="${direction(market.return5d)}">${signed(market.return5d)}</b></span>
      <span><small>20D</small><b class="${direction(market.return20d)}">${signed(market.return20d)}</b></span>
      <span><small>狀態</small><b>${market.session === "open" ? "交易中" : market.session === "closed" ? "已收盤" : "等待開盤"}</b></span>
    </div>
    <p class="market-detail__asof">${escapeHtml(market.tradeDate)} · ${escapeHtml(market.asOf)} · ${escapeHtml(market.source)}</p>`;
}

export function renderTaiwanBreadth(breadth: TaiwanBreadth): string {
  const total = breadth.advancers + breadth.unchanged + breadth.decliners;
  const advance = (breadth.advancers / total) * 100;
  const flat = (breadth.unchanged / total) * 100;
  const decline = 100 - advance - flat;
  return `
    <section class="dash-panel breadth" aria-labelledby="breadth-title">
      <div class="dash-panel__head">
        <div><p class="dash-kicker">TAIWAN BREADTH / 02</p><h2 id="breadth-title">台股盤面結構</h2></div>
        <span class="dash-chip">上市＋上櫃</span>
      </div>
      <div class="breadth__hero">
        <div class="breadth__index"><span>TAIEX</span><strong>${breadth.indexClose.toLocaleString("en-US")}</strong><em class="${direction(breadth.indexChange1d)}">${signed(breadth.indexChange1d)}</em></div>
        <p>${escapeHtml(describeBreadthPanel(breadth))}</p>
      </div>
      <div class="breadth-bar" aria-label="上漲 ${breadth.advancers} 家，平盤 ${breadth.unchanged} 家，下跌 ${breadth.decliners} 家">
        <span class="breadth-bar__up" style="width:${advance.toFixed(2)}%"></span>
        <span class="breadth-bar__flat" style="width:${flat.toFixed(2)}%"></span>
        <span class="breadth-bar__down" style="width:${decline.toFixed(2)}%"></span>
      </div>
      <div class="breadth-counts">
        <span><i class="up"></i><small>上漲</small><b>${breadth.advancers}</b></span>
        <span><i></i><small>平盤</small><b>${breadth.unchanged}</b></span>
        <span><i class="down"></i><small>下跌</small><b>${breadth.decliners}</b></span>
      </div>
      <div class="metric-strip">
        <span><small>漲停 / 跌停</small><b>${breadth.limitUp} / ${breadth.limitDown}</b></span>
        <span><small>成交值</small><b>${breadth.turnoverE.toLocaleString("en-US", { maximumFractionDigits: 1 })} 億</b></span>
        <span><small>相對 20 日</small><b>${breadth.turnoverVs20d.toFixed(2)}×</b></span>
        <span><small>A / D</small><b>${(breadth.advancers / breadth.decliners).toFixed(2)}</b></span>
      </div>
    </section>`;
}

export function renderIndustries(industries: readonly IndustryBreadth[]): string {
  const rows = industries
    .map((industry) => {
      const total = industry.advancers + industry.unchanged + industry.decliners;
      const advance = (industry.advancers / total) * 100;
      const flat = (industry.unchanged / total) * 100;
      const decline = 100 - advance - flat;
      return `
        <div class="industry-row" data-industry="${escapeHtml(industry.id)}">
          <span class="industry-row__name">${escapeHtml(industry.name)}</span>
          <div class="industry-row__bar" aria-label="${escapeHtml(industry.name)}上漲 ${industry.advancers}、平盤 ${industry.unchanged}、下跌 ${industry.decliners}">
            <i class="industry-row__up" style="width:${advance.toFixed(2)}%"></i>
            <i class="industry-row__flat" style="width:${flat.toFixed(2)}%"></i>
            <i class="industry-row__down" style="width:${decline.toFixed(2)}%"></i>
          </div>
          <span class="industry-row__counts">${industry.advancers}<i>/</i>${industry.decliners}</span>
          <em class="${direction(industry.return1d)}">${signed(industry.return1d)}</em>
        </div>`;
    })
    .join("");
  return `
    <section class="dash-panel industries" aria-labelledby="industries-title">
      <div class="dash-panel__head">
        <div><p class="dash-kicker">INDUSTRY X-RAY / 03</p><h2 id="industries-title">產業漲跌家數</h2></div>
        <span class="dash-chip">官方產業分類</span>
      </div>
      <div class="industry-legend"><span>上漲</span><span>平盤</span><span>下跌</span><span>漲 / 跌家數</span></div>
      <div class="industry-list">${rows}</div>
    </section>`;
}


/**
 * 成交熱度泡泡圖：取代原本的「主題板塊圖」。
 *
 * 為什麼換掉主題板塊圖：那塊需要「AI 概念股」這類主題分類，但 tickers 只有法定
 * 產業別，整塊資料只能寫死 mock。成交值是 chip_data 現成的真實資料，換成泡泡圖
 * 之後這個面板從全假變成全真。
 *
 * 面積正比於成交值，故直徑取平方根——直徑若直接正比於成交值，台積電會把版面吃掉，
 * 而且人眼判讀圓形大小本來就是看面積不是看直徑，直接正比會嚴重誇大差距。
 */
/** 泡泡在版面座標系中的位置與半徑（單位＝舞台寬度的 1%）。 */
interface PackedBubble {
  readonly x: number;
  readonly y: number;
  readonly r: number;
}

/**
 * 用黃金角螺旋替每顆泡泡找不重疊的位置：從中心往外走，第一個放得下的點就用。
 *
 * ⚠️ 刻意不用亂數。亂數版面每次重畫都會跳位（點一下泡泡就重排，很難用），
 * 而且沒辦法寫測試。黃金角本身就夠不規則，排出來不會有明顯的行列或花瓣感。
 */
export function packBubbles(
  radii: readonly number[],
  width: number,
  height: number,
  gap = 2.4,
): PackedBubble[] {
  const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));   // ≈137.5°
  const cx = width / 2;
  const cy = height / 2;
  const squash = height / width;      // 舞台是直式的，螺旋跟著拉成橢圓才填得滿
  const placed: PackedBubble[] = [];

  for (const r of radii) {
    let spot: PackedBubble | undefined;
    // 先用要求的間距找；真的塞不下就逐步放寬到 0，寧可貼在一起也不要疊在中心。
    // （早期版本失敗時直接退回中心點，結果 24 顆全部重疊成一坨——只要有一顆
    //   找不到位置，畫面就毀了，所以這裡一定要有降級路徑。）
    for (let relax = 0; relax < 4 && !spot; relax += 1) {
      const slack = gap * (1 - relax / 4);
      for (let step = 0; step < 4000 && !spot; step += 1) {
        const angle = step * GOLDEN_ANGLE;
        const reach = 0.85 * Math.sqrt(step);
        const x = cx + reach * Math.cos(angle);
        const y = cy + reach * Math.sin(angle) * squash;
        if (x - r < 0 || x + r > width || y - r < 0 || y + r > height) continue;
        const overlaps = placed.some(
          (other) => Math.hypot(other.x - x, other.y - y) < other.r + r + slack,
        );
        if (!overlaps) spot = { x, y, r };
      }
    }
    placed.push(spot ?? { x: cx, y: cy, r });
  }
  return placed;
}

export function renderTurnoverBubbles(bubbles: readonly TurnoverBubble[]): string {
  if (!bubbles.length) {
    return "";
  }
  const ranked = [...bubbles].sort((left, right) => right.turnoverE - left.turnoverE);
  const max = ranked[0]?.turnoverE ?? 0;

  // 舞台座標：寬 100 單位、高 STAGE_H 單位（CSS 用同樣的 aspect-ratio）。
  // 位置與尺寸全部以百分比輸出，整塊會跟著容器等比縮放，任何視窗寬度都不會疊到。
  const STAGE_W = 100;
  const STAGE_H = 128;

  // 最大泡泡的半徑由「總面積佔舞台幾成」反推，而不是寫死。
  // 寫死的話檔數一變就出事：實測 24 檔配 R=21 會吃掉舞台 71%，貪婪螺旋根本塞不下。
  // 目標 42%——貪婪排列達不到理論極限，留餘裕才不會擠成一團。
  const sumRatio = max > 0 ? ranked.reduce((s, b) => s + b.turnoverE / max, 0) : 1;
  const R_MAX = Math.min(21, Math.sqrt((0.42 * STAGE_W * STAGE_H) / (Math.PI * sumRatio)));

  // 面積嚴格正比於成交值 → 半徑取平方根，且不設下限。
  // （上一版為了塞得下股名有 58px 下限，那會讓小泡泡虛胖、比例失真；
  //   改成 12 檔之後最小的直徑仍有最大的 58%，字放得下，就不需要下限了。）
  const radii = ranked.map((b) => (max > 0 ? R_MAX * Math.sqrt(b.turnoverE / max) : R_MAX));
  const packed = packBubbles(radii, STAGE_W, STAGE_H);

  const items = ranked.map((b, index) => {
    const spot = packed[index];
    if (!spot) return "";
    return `
      <button type="button" class="bubble bubble--${direction(b.change1d)}"
        data-stock="${escapeHtml(b.ticker)}"
        style="--bubble-d:${(spot.r * 2).toFixed(2)}%;--bubble-x:${(spot.x / STAGE_W * 100).toFixed(2)}%;--bubble-y:${(spot.y / STAGE_H * 100).toFixed(2)}%"
        aria-label="成交值第 ${index + 1} 名 ${escapeHtml(b.name)} ${escapeHtml(b.ticker)}，${Math.round(b.turnoverE)} 億元，${signed(b.change1d)}">
        <span class="bubble__gloss" aria-hidden="true"></span>
        <span class="bubble__text">
          <b>${escapeHtml(b.name)}</b>
          <em>${signed(b.change1d)}</em>
          <small>${Math.round(b.turnoverE)} 億</small>
        </span>
      </button>`;
  }).join("");

  const total = Math.round(ranked.reduce((sum, b) => sum + b.turnoverE, 0));
  return `
    <section class="dash-panel bubble-map" aria-labelledby="bubble-title">
      <div class="dash-panel__head">
        <div><p class="dash-kicker">TURNOVER HEAT / 04</p><h2 id="bubble-title">成交熱度</h2></div>
        <div class="dash-legend"><span>面積＝成交值</span><span class="up">▲ 上漲</span><span class="down">▼ 下跌</span></div>
      </div>
      <p class="bubble-map__note">當日成交值前 ${ranked.length} 大個股，合計 ${total.toLocaleString("en-US")} 億元。資金流向哪裡，泡泡就大。</p>
      <div class="bubble-map__field"><div class="bubble-map__stage">${items}</div></div>
    </section>`;
}
