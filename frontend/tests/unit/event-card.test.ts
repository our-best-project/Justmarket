import { describe, expect, it } from "vitest";

import { getMarketReactionLabel, renderScore } from "../../src/components";
import { eventsFixture } from "../fixtures/events.fixture";

describe("event card market validation score", () => {
  it("labels scores by market reaction instead of verification maturity", () => {
    expect(getMarketReactionLabel(0)).toBe("市場持相反意見");
    expect(getMarketReactionLabel(44)).toBe("市場持相反意見");
    expect(getMarketReactionLabel(45)).toBe("市場反應普通");
    expect(getMarketReactionLabel(59)).toBe("市場反應普通");
    expect(getMarketReactionLabel(60)).toBe("市場反應一致");
    expect(getMarketReactionLabel(100)).toBe("市場反應一致");
  });

  it("shows the scoring logic next to a scored event", () => {
    const html = renderScore({
      ...eventsFixture.events[0],
      market_validation: 30,
      verify_state: "verified",
    });

    expect(html).toContain("市場持相反意見");
    expect(html).not.toContain("高度一致");
    expect(html).toContain("score__help");
    expect(html).toContain("法人買賣超最高影響 ±25 分");
    expect(html).toContain("0–44 分");
  });

  it("shows neutral events explicitly instead of calling them observing", () => {
    const html = renderScore({
      ...eventsFixture.events[0],
      market_validation: null,
      expected_direction: "中性",
    });

    expect(html).toContain("中性事件");
    expect(html).not.toContain("觀察中");
    expect(html).toContain("查看中性事件說明");
    expect(html).toContain("沒有明確的利多或利空方向");
    expect(html).toContain("三大法人資料仍會如實呈現");
  });
});

describe("renderStars（重要性星等）", () => {
  it("滿星實心、缺星空心，合計恆為五顆", async () => {
    const { renderStars } = await import("../../src/components/event-card");
    expect(renderStars(4)).toContain("★★★★☆");
    expect(renderStars(1)).toContain("★☆☆☆☆");
  });

  it("越界值截到 0–5，不會畫出六顆星或負數", async () => {
    const { renderStars } = await import("../../src/components/event-card");
    expect(renderStars(7)).toContain("★★★★★");
    expect(renderStars(0)).toContain("☆☆☆☆☆");
  });

  it("讀屏拿得到數字（aria-label）", async () => {
    const { renderStars } = await import("../../src/components/event-card");
    expect(renderStars(3)).toContain('aria-label="重要性 3 星');
  });
});
