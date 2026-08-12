import { describe, expect, it } from "vitest";

import { normalizeBasePath } from "../../vite.config";

describe("normalizeBasePath", () => {
  it.each([
    [undefined, "/"],
    ["", "/"],
    ["/", "/"],
    [".", "./"],
    ["./", "./"],
    ["justmarket", "/justmarket/"],
    ["/justmarket", "/justmarket/"],
    ["/justmarket/", "/justmarket/"],
  ])("normalizes %s to %s", (input, expected) => {
    expect(normalizeBasePath(input)).toBe(expected);
  });
});
