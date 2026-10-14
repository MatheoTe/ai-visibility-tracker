export type RangeKey = "7d" | "30d" | "90d";

export const RANGES: { key: RangeKey; label: string; days: number }[] = [
  { key: "7d", label: "7 days", days: 7 },
  { key: "30d", label: "30 days", days: 30 },
  { key: "90d", label: "90 days", days: 90 },
];

const iso = (d: Date) => d.toISOString().slice(0, 10);

export function rangeFor(key: RangeKey, today: Date = new Date()): { from: string; to: string } {
  const days = RANGES.find((r) => r.key === key)?.days ?? 30;
  const to = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()));
  const from = new Date(to);
  from.setUTCDate(to.getUTCDate() - (days - 1));
  return { from: iso(from), to: iso(to) };
}
