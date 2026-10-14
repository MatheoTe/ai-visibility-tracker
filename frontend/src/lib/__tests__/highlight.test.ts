import { describe, expect, it } from "vitest";

import { segment } from "@/lib/highlight";

const join = (segs: { text: string }[]) => segs.map((s) => s.text).join("");

describe("segment", () => {
  it("splits text around spans without losing characters", () => {
    const text = "Try Lumora or Pipewise today.";
    const segs = segment(text, [
      { start: 14, end: 22, brandId: "p" },
      { start: 4, end: 10, brandId: "l" },
    ]);
    expect(segs).toEqual([
      { text: "Try ", brandId: null },
      { text: "Lumora", brandId: "l" },
      { text: " or ", brandId: null },
      { text: "Pipewise", brandId: "p" },
      { text: " today.", brandId: null },
    ]);
    expect(join(segs)).toBe(text);
  });

  it("drops overlapping and empty spans and clips out-of-range ones", () => {
    const text = "abcdefgh";
    const segs = segment(text, [
      { start: 2, end: 5, brandId: "a" },
      { start: 4, end: 7, brandId: "b" }, // overlaps the first
      { start: 6, end: 6, brandId: "c" }, // empty
      { start: 7, end: 99, brandId: "d" }, // past the end
    ]);
    expect(join(segs)).toBe(text);
    expect(segs.filter((s) => s.brandId).map((s) => s.brandId)).toEqual(["a", "d"]);
  });

  it("returns the whole text when there are no spans", () => {
    expect(segment("hello", [])).toEqual([{ text: "hello", brandId: null }]);
  });
});
