import { describe, expect, it } from "vitest";
import {
  advanceShare, describeBreadthPanel, describeMarket, isDiverging,
} from "../../src/components/market-narrative";
import type { TaiwanBreadth } from "../../src/types";

function breadth(over: Partial<TaiwanBreadth> = {}): TaiwanBreadth {
  return {
    indexClose: 44396.7, indexChange1d: -0.48,
    advancers: 672, decliners: 562, unchanged: 107,
    limitUp: 38, limitDown: 4, turnoverE: 11119.1,
    turnoverVs20d: 0.93, medianReturn: 0.1,
    source: "TWSE 收盤資料（FinMind）", asOf: "2026-08-06", ...over,
  };
}

describe("背離判定", () => {
  it("指數跌但上漲家數較多 → 背離（2026-08-06 的實際盤面）", () => {
    expect(isDiverging(breadth())).toBe(true);
  });

  it("指數漲且上漲家數較多 → 不背離", () => {
    expect(isDiverging(breadth({ indexChange1d: 0.6 }))).toBe(false);
  });

  it("指數平盤不算背離——0 沒有方向可言", () => {
    expect(isDiverging(breadth({ indexChange1d: 0 }))).toBe(false);
  });
});

describe("describeMarket（標題導言）", () => {
  it("敘述與家數一致，不會出現與資料矛盾的說法", () => {
    const text = describeMarket(breadth());
    expect(text).toContain("上漲 672 家");
    expect(text).toContain("下跌 562 家");
    expect(text).toContain("54%");
    // 原本寫死的文案說「上漲家數尚未過半」，但實際已過半——這種話不可以再出現
    expect(text).not.toContain("尚未過半");
  });

  it("指數與家數反向時點名是權值股在跌", () => {
    expect(describeMarket(breadth())).toContain("跌的是權值股");
  });

  it("同向時不硬掰背離", () => {
    const text = describeMarket(breadth({ indexChange1d: 0.6 }));
    expect(text).toContain("指數同向收 +0.60%");
    expect(text).not.toContain("相反");
  });

  it("量能用門檻描述，不是固定講法", () => {
    expect(describeMarket(breadth({ turnoverVs20d: 1.45 }))).toContain("放大");
    expect(describeMarket(breadth({ turnoverVs20d: 0.62 }))).toContain("萎縮");
    expect(describeMarket(breadth({ turnoverVs20d: 1.0 }))).toContain("為 20 日均值");
  });

  it("不含任何預測或建議字眼（專案的法規底線）", () => {
    const forbidden = ["建議", "看好", "看壞", "布局", "買進", "賣出", "目標價", "後市", "可望", "預期"];
    for (const sample of [breadth(), breadth({ indexChange1d: 1.2 }), breadth({ advancers: 100, decliners: 900 })]) {
      const text = describeMarket(sample);
      for (const word of forbidden) expect(text).not.toContain(word);
    }
  });
});

describe("describeBreadthPanel（盤面面板）", () => {
  it("指數跌但多數個股收紅 → 說是權值股在拖，不是講反的「權值股撐住」", () => {
    const text = describeBreadthPanel(breadth());
    expect(text).toContain("是權值股在拖");
    expect(text).not.toContain("撐住");
  });

  it("指數漲但多數個股收黑 → 才是權值股撐住", () => {
    const text = describeBreadthPanel(breadth({ indexChange1d: 0.8, advancers: 400, decliners: 800 }));
    expect(text).toContain("撐住");
  });
});

describe("advanceShare", () => {
  it("平盤不列入分母", () => {
    expect(advanceShare(breadth({ advancers: 600, decliners: 400, unchanged: 1000 }))).toBeCloseTo(0.6);
  });

  it("沒有任何漲跌時退回五五分，不會除以零", () => {
    expect(advanceShare(breadth({ advancers: 0, decliners: 0 }))).toBe(0.5);
  });
});
