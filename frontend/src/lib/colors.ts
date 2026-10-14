/**
 * Categorical series colors, assigned by entity (brand) - never by rank - so a
 * filter that hides a brand doesn't repaint the others. Order and steps come from
 * a palette validated for color-vision deficiency on both light and dark surfaces.
 * The primary brand always takes slot 1.
 */
export const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)",
  "var(--series-5)", "var(--series-6)", "var(--series-7)", "var(--series-8)"] as const;

export interface BrandLike {
  id: string;
  role: "primary" | "competitor";
  name: string;
}

export function brandColors(brands: BrandLike[]): Map<string, string> {
  const ordered = [...brands].sort((a, b) =>
    a.role === b.role ? a.name.localeCompare(b.name) : a.role === "primary" ? -1 : 1,
  );
  const map = new Map<string, string>();
  ordered.forEach((b, i) => map.set(b.id, i < SERIES.length ? SERIES[i] : "var(--series-other)"));
  return map;
}
