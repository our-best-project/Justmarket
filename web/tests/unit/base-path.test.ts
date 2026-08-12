import { describe, expect, it } from "vitest";

import { normalizeBasePath } from "../../vite.config";

describe("normalizeBasePath", () => {
  it.each([
    [undefined, "/"],
    ["", "/"],
    ["/", "/"],
    [".", "./"],
    ["./", "./"],
    ["event-signal", "/event-signal/"],
    ["/event-signal", "/event-signal/"],
    ["/event-signal/", "/event-signal/"],
  ])("normalizes %s to %s", (input, expected) => {
    expect(normalizeBasePath(input)).toBe(expected);
  });
});
