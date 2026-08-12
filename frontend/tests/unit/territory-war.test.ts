import { describe, expect, it } from "vitest";
import { bullShare, fitCamera, rawProject, renderTerritoryWar } from "../../src/components/territory-war";
import type { TaiwanBreadth } from "../../src/types";

function breadth(over: Partial<TaiwanBreadth> = {}): TaiwanBreadth {
  return {
    asOf: "2026-08-06", indexClose: 24680, indexChange1d: 0.42,
    advancers: 540, decliners: 460, unchanged: 120,
    limitUp: 12, limitDown: 3, medianReturn: 0.15,
    turnoverE: 48200, turnoverVs20d: 1.08, ...over,
  } as TaiwanBreadth;
}

describe("bullShare", () => {
  it("平盤不列入——它既不是紅方也不是綠方的領土", () => {
    // 上漲 600、下跌 400、平盤 1000：若把平盤算進分母會變成 30%，嚴重低估多方
    expect(bullShare(breadth({ advancers: 600, decliners: 400, unchanged: 1000 }))).toBeCloseTo(0.6);
  });

  it("全面上漲時多方佔滿", () => {
    expect(bullShare(breadth({ advancers: 900, decliners: 0 }))).toBe(1);
  });

  it("一家都沒漲沒跌時退回五五分，不會除以零", () => {
    const share = bullShare(breadth({ advancers: 0, decliners: 0, unchanged: 900 }));
    expect(Number.isFinite(share)).toBe(true);
    expect(share).toBe(0.5);
  });
});

describe("renderTerritoryWar", () => {
  it("標示的百分比與漲跌家數一致", () => {
    const html = renderTerritoryWar(breadth({ advancers: 540, decliners: 460 }));
    expect(html).toContain("54%");     // 540 / (540+460)
    expect(html).toContain("46%");
  });

  it("圖例列出三種家數，含平盤（畫面上沒有平盤領土，數字要補回來）", () => {
    const html = renderTerritoryWar(breadth({ advancers: 540, decliners: 460, unchanged: 120 }));
    expect(html).toContain("上漲 540");
    expect(html).toContain("下跌 460");
    expect(html).toContain("平盤 120");
  });

  it("寫明領土只是漲跌家數，不是預測——避免被讀成投資建議", () => {
    const html = renderTerritoryWar(breadth());
    expect(html).toContain("不代表任何預測");
  });

  it("canvas 有替代文字，關掉動畫或用讀屏也知道戰況", () => {
    const html = renderTerritoryWar(breadth({ advancers: 540, decliners: 460 }));
    expect(html).toMatch(/aria-label="[^"]*540[^"]*460[^"]*"/);
  });
});

describe("fitCamera（戰場不被畫布切到）", () => {
  const cam = () => ({ yaw: 0.35, pitch: 0.45, dist: 620, fov: 520 });
  const FIELD_X = 360;
  const FIELD_Z = 90;
  const TOP_EXTRA = 26 * 2.1;   // 最高的樹
  const PAD = 8;

  /** 把戰場八個極端點投影到畫面，回傳含樹梢在內的外接矩形。 */
  function bounds(width: number, height: number) {
    const c = cam();
    const fit = fitCamera(c, width, height);
    c.fov = fit.fov;
    let l = Infinity; let r = -Infinity; let t = Infinity; let b = -Infinity;
    for (const x of [-FIELD_X, FIELD_X]) {
      for (const z of [-FIELD_Z, FIELD_Z]) {
        for (const y of [0, 26]) {
          const p = rawProject(c, x, y, z);
          const s = c.fov / p.d;
          const sx = width * fit.horizAnchor + p.cx * s;
          const sy = height * fit.vertAnchor - p.cy * s;
          l = Math.min(l, sx); r = Math.max(r, sx);
          t = Math.min(t, sy - TOP_EXTRA * s);   // 樹梢
          b = Math.max(b, sy + 8 * s);           // 影子
        }
      }
    }
    return { l, r, t, b, fit };
  }

  // 儀表板實際會遇到的比例：桌面滿寬、平板、窄欄、以及退化的極端值
  const SIZES: ReadonlyArray<readonly [number, number]> = [
    [1214, 360], [681, 360], [420, 360], [1600, 360], [900, 240], [300, 300],
  ];

  it.each(SIZES)("%i×%i 不會有任何一角出界", (w, h) => {
    const { l, r, t, b } = bounds(w, h);
    expect(l).toBeGreaterThanOrEqual(-0.5);
    expect(r).toBeLessThanOrEqual(w + 0.5);
    expect(t).toBeGreaterThanOrEqual(-0.5);
    expect(b).toBeLessThanOrEqual(h + 0.5);
  });

  it.each(SIZES)("%i×%i 至少有一軸貼齊留白，沒有白白縮小", (w, h) => {
    const { l, r, t, b } = bounds(w, h);
    // 綁住縮放的那一軸，兩側留白應該恰好等於 PAD
    const tightX = Math.abs(l - PAD) < 1 && Math.abs(w - r - PAD) < 1;
    const tightY = Math.abs(t - PAD) < 1 && Math.abs(h - b - PAD) < 1;
    expect(tightX || tightY).toBe(true);
  });

  it("非綁定軸的餘裕平均分在兩側（置中，不是靠邊）", () => {
    const { l, r, t, b } = bounds(1600, 360);   // 很寬 → 高度綁定、寬度有餘裕
    expect(Math.abs(l - (1600 - r))).toBeLessThan(1);
    expect(t).toBeCloseTo(PAD, 0);
    expect(360 - b).toBeCloseTo(PAD, 0);
  });
});

describe("地面文字方向", () => {
  const cam = { yaw: 0.35, pitch: 0.45, dist: 620, fov: 780 };

  /** 重現 groundText 取的兩個局部軸（含方向修正）。 */
  function textAxes(wx: number) {
    const screen = (x: number, z: number) => {
      const p = rawProject(cam, x, 0, z);
      const s = cam.fov / p.d;
      return { x: p.cx * s, y: -p.cy * s };
    };
    const p0 = screen(wx, 0);
    const ax = screen(wx + 10, 0);
    const az = screen(wx, 10);
    let a = (ax.x - p0.x) / 10;
    let b = (ax.y - p0.y) / 10;
    let c = (az.x - p0.x) / 10;
    let d = (az.y - p0.y) / 10;
    if (d < 0) { c = -c; d = -d; }
    if (a < 0) { a = -a; b = -b; }
    return { a, b, c, d };
  }

  it.each([-200, 0, 200])("wx=%i：字由左讀到右、字高朝下（不是顛倒的）", (wx) => {
    const { a, d } = textAxes(wx);
    expect(a).toBeGreaterThan(0);   // 局部 +x → 螢幕右
    expect(d).toBeGreaterThan(0);   // 局部 +y → 螢幕下
  });

  it("行列式為正——沒有鏡像，字不會左右相反", () => {
    const { a, b, c, d } = textAxes(0);
    expect(a * d - b * c).toBeGreaterThan(0);
  });
});
