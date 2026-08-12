import { describe, expect, it } from "vitest";
import { packBubbles, renderTurnoverBubbles } from "../../src/components/dashboard";
import type { TurnoverBubble } from "../../src/types";

function bubble(over: Partial<TurnoverBubble> = {}): TurnoverBubble {
  return {
    ticker: "2330", name: "台積電", industry: "半導體業",
    close: 1105, turnoverE: 600, change1d: -1.66, ...over,
  };
}

/** 泡泡直徑（舞台寬的百分比）：從 style="--bubble-d:NN.NN%" 取回 */
function diameters(html: string): number[] {
  return [...html.matchAll(/--bubble-d:([\d.]+)%/g)].map((m) => Number(m[1]));
}

describe("packBubbles", () => {
  const radii = [21, 17, 15, 14, 12, 11, 10, 10, 9, 9, 8, 8];

  it("排出來的泡泡兩兩不重疊", () => {
    const placed = packBubbles(radii, 100, 128);
    for (let i = 0; i < placed.length; i += 1) {
      for (let j = i + 1; j < placed.length; j += 1) {
        const [a, b] = [placed[i]!, placed[j]!];
        const distance = Math.hypot(a.x - b.x, a.y - b.y);
        expect(distance).toBeGreaterThanOrEqual(a.r + b.r - 1e-6);
      }
    }
  });

  it("全部落在舞台範圍內（不會被面板裁掉）", () => {
    for (const p of packBubbles(radii, 100, 128)) {
      expect(p.x - p.r).toBeGreaterThanOrEqual(0);
      expect(p.x + p.r).toBeLessThanOrEqual(100);
      expect(p.y - p.r).toBeGreaterThanOrEqual(0);
      expect(p.y + p.r).toBeLessThanOrEqual(128);
    }
  });

  it("同一組輸入永遠排出同一個版面（不能用亂數，否則每次重畫都跳位）", () => {
    expect(packBubbles(radii, 100, 128)).toEqual(packBubbles(radii, 100, 128));
  });

  it("位置有打散，不是排成整齊的行列", () => {
    const placed = packBubbles(radii, 100, 128);
    // 若排成列，y 座標會集中在少數幾個值；打散後幾乎每顆都不同高
    const levels = new Set(placed.map((p) => Math.round(p.y / 4)));
    expect(levels.size).toBeGreaterThan(placed.length / 2);
  });
});

describe("renderTurnoverBubbles", () => {
  it("空清單不畫區塊（後端沒回 topTurnover 時不留空殼）", () => {
    expect(renderTurnoverBubbles([])).toBe("");
  });

  it("面積嚴格正比於成交值——沒有下限墊高小泡泡", () => {
    // 直接拿成交值當直徑的話，4 倍成交值會畫成 16 倍面積，視覺上嚴重誇大。
    const d = diameters(renderTurnoverBubbles([
      bubble({ ticker: "A", turnoverE: 400 }),
      bubble({ ticker: "B", turnoverE: 200 }),
      bubble({ ticker: "C", turnoverE: 100 }),
    ]));
    const k = d.map((size, i) => (size * size) / [400, 200, 100][i]!);
    // 面積 / 成交值 應為定值；只容許輸出時取到小數第二位的誤差
    expect(Math.max(...k) / Math.min(...k)).toBeLessThan(1.01);
  });

  it("由大到小輸出，最大的排第一（也是螢幕報讀的順序）", () => {
    const html = renderTurnoverBubbles([
      bubble({ ticker: "SMALL", turnoverE: 100 }),
      bubble({ ticker: "BIG", turnoverE: 500 }),
    ]);
    expect(html.indexOf('data-stock="BIG"')).toBeLessThan(html.indexOf('data-stock="SMALL"'));
  });

  it("台股慣例：漲紅跌綠，平盤第三色", () => {
    const html = renderTurnoverBubbles([
      bubble({ change1d: 2.1 }),
      bubble({ ticker: "2317", change1d: -1.2 }),
      bubble({ ticker: "2454", change1d: 0 }),
    ]);
    expect(html).toContain("bubble--up");
    expect(html).toContain("bubble--down");
    expect(html).toContain("bubble--flat");
  });

  it("帶 data-stock 以沿用既有選取連動（board.ts 的事件委派靠這個屬性）", () => {
    expect(renderTurnoverBubbles([bubble()])).toContain('data-stock="2330"');
  });

  it("成交值為 0 不會產生 NaN 尺寸或座標", () => {
    const html = renderTurnoverBubbles([bubble({ turnoverE: 0 })]);
    expect(html).not.toContain("NaN");
    expect(diameters(html)[0]).toBeGreaterThanOrEqual(0);
  });
});
