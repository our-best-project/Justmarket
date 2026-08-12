import { describe, expect, it } from "vitest";

import { getNavigationRoute, parseRoute } from "../../src/router";

describe("hash router", () => {
  it.each([
    ["", { name: "home" }],
    ["#home", { name: "home" }],
    ["#board", { name: "board" }],
    ["#all", { name: "all" }],
    ["#watchlist", { name: "watchlist" }],
    ["#method", { name: "method" }],
    ["#event-EVT-001", { name: "detail", id: "EVT-001" }],
    ["#unknown", { name: "home" }],
  ])("parses %s", (hash, expected) => {
    expect(parseRoute(hash)).toEqual(expected);
  });

  it("highlights the all-events navigation for detail routes", () => {
    expect(getNavigationRoute(parseRoute("#event-EVT-001"))).toBe("all");
  });
});
