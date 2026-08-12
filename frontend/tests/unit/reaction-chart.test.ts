import { describe, expect, it } from "vitest";

import { renderMarketReactionChart } from "../../src/components";
import type { MarketReaction } from "../../src/types";

const reaction: MarketReaction = {
  ticker: "2330",
  event_index: 1,
  event_date: "2026-07-15",
  as_of: "2026-07-16",
  return_1d: 0.018,
  return_3d: null,
  return_5d: null,
  volume_ratio: 1.8,
  foreign_consecutive_days: 2,
  series: [
    {
      date: "2026-07-14",
      close: 1040,
      volume_ratio: 1.1,
      foreign_net: -2_000_000,
      trust_net: 500_000,
      dealer_net: 0,
    },
    {
      date: "2026-07-15",
      close: 1060,
      volume_ratio: 1.8,
      foreign_net: 3_000_000,
      trust_net: -600_000,
      dealer_net: 100_000,
    },
  ],
};

describe("market reaction chart", () => {
  it("renders actual closing prices with date and price axes", () => {
    const html = renderMarketReactionChart(reaction);

    expect(html).toContain("每日收盤價");
    expect(html).toContain("股價（元）");
    expect(html).toContain("07/14");
    expect(html).toContain("1,040");
  });

  it("renders one row per institution with buy and sell states", () => {
    const html = renderMarketReactionChart(reaction);

    expect(html).toContain("外資");
    expect(html).toContain("投信");
    expect(html).toContain("自營商");
    expect(html).toContain("inst-cell--buy");
    expect(html).toContain("inst-cell--sell");
    expect(html).toContain("買超");
    expect(html).toContain("賣超");
  });
});
