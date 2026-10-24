import { describe, expect, it } from "vitest";
import { BOTTOM_SLACK_PX, isNearBottom } from "./logScroll";

describe("isNearBottom", () => {
  it("is true when scrolled to the end", () => {
    expect(isNearBottom({ scrollTop: 900, clientHeight: 100, scrollHeight: 1000 })).toBe(true);
  });

  it("allows the default slack below the end", () => {
    const scrollTop = 1000 - 100 - BOTTOM_SLACK_PX;
    expect(isNearBottom({ scrollTop, clientHeight: 100, scrollHeight: 1000 })).toBe(true);
  });

  it("is false once the user has scrolled up past the slack", () => {
    expect(isNearBottom({ scrollTop: 500, clientHeight: 100, scrollHeight: 1000 })).toBe(false);
  });

  it("is true when the content does not overflow", () => {
    expect(isNearBottom({ scrollTop: 0, clientHeight: 300, scrollHeight: 300 })).toBe(true);
  });
});
