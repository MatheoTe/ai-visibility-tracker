import { describe, expect, it } from "vitest";

import { modelLabel, pct, position, ppDelta, relativeTime, sentiment, shortDay, usd } from "@/lib/format";

describe("format", () => {
  it("formats percentages and missing values", () => {
    expect(pct(0.1234)).toBe("12.3%");
    expect(pct(0.5, 0)).toBe("50%");
    expect(pct(null)).toBe("—");
  });

  it("formats percentage-point deltas with an explicit sign", () => {
    expect(ppDelta(0.024)).toBe("+2.4 pp");
    expect(ppDelta(-0.031)).toBe("−3.1 pp");
    expect(ppDelta(0.0001)).toBe("±0.0 pp");
    expect(ppDelta(undefined)).toBeNull();
  });

  it("formats positions, sentiment and money", () => {
    expect(position(1.53)).toBe("#1.5");
    expect(sentiment(0.25)).toBe("+0.25");
    expect(sentiment(-0.5)).toBe("-0.50");
    expect(usd("0.000123")).toBe("$0.0001");
    expect(usd("1234.5")).toBe("$1,234.50");
  });

  it("strips the provider from model keys and formats days", () => {
    expect(modelLabel("anthropic:claude-sonnet-5-5")).toBe("claude-sonnet-5-5");
    expect(shortDay("2026-10-04")).toBe("10/4");
  });

  it("formats relative time", () => {
    const now = new Date("2026-10-14T12:00:00Z");
    expect(relativeTime("2026-10-14T11:59:30Z", now)).toBe("just now");
    expect(relativeTime("2026-10-14T11:15:00Z", now)).toBe("45m ago");
    expect(relativeTime("2026-10-12T12:00:00Z", now)).toBe("2d ago");
  });
});
