import { describe, expect, it } from "vitest";

import { brandColors } from "@/lib/colors";
import { rangeFor } from "@/lib/dates";
import { pivotByModel, pivotSeries } from "@/lib/series";

describe("pivotSeries", () => {
  it("turns long points into one sorted row per day", () => {
    const point = (day: string, brand_id: string, share_of_voice: number) => ({
      day,
      brand_id,
      share_of_voice,
      mention_rate: 0,
      avg_position: null,
      sentiment: null,
    });
    const rows = pivotSeries(
      [point("2026-10-02", "a", 0.6), point("2026-10-01", "a", 0.5), point("2026-10-01", "b", 0.5)],
      "share_of_voice",
    );
    expect(rows).toEqual([
      { day: "2026-10-01", a: 0.5, b: 0.5 },
      { day: "2026-10-02", a: 0.6 },
    ]);
  });

  it("pivots per-model breakdowns", () => {
    const rows = pivotByModel(
      [
        { model_key: "openai:m", brand_id: "a", responses: 10, mention_rate: 0.3, avg_position: 1, sentiment: 0 },
        { model_key: "openai:m", brand_id: "b", responses: 10, mention_rate: 0.1, avg_position: 2, sentiment: 0 },
      ],
      "mention_rate",
    );
    expect(rows).toEqual([{ day: "openai:m", a: 0.3, b: 0.1 }]);
  });
});

describe("brandColors", () => {
  const brands = [
    { id: "z", name: "Zeta", role: "competitor" as const },
    { id: "p", name: "Primary", role: "primary" as const },
    { id: "a", name: "Alpha", role: "competitor" as const },
  ];

  it("gives the primary brand slot 1 and competitors stable alphabetical slots", () => {
    const colors = brandColors(brands);
    expect(colors.get("p")).toBe("var(--series-1)");
    expect(colors.get("a")).toBe("var(--series-2)");
    expect(colors.get("z")).toBe("var(--series-3)");
  });

  it("keeps earlier brands' colors when a later-sorted brand is removed", () => {
    // Removing a later brand never repaints earlier ones.
    const colors = brandColors(brands.filter((b) => b.id !== "z"));
    expect(colors.get("a")).toBe("var(--series-2)");
  });
});

describe("rangeFor", () => {
  it("returns inclusive UTC day ranges", () => {
    expect(rangeFor("7d", new Date("2026-10-14T23:30:00Z"))).toEqual({ from: "2026-10-08", to: "2026-10-14" });
    expect(rangeFor("30d", new Date("2026-03-01T00:00:00Z"))).toEqual({ from: "2026-01-31", to: "2026-03-01" });
  });
});
