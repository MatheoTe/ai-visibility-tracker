import type { Overview } from "@/lib/api/schemas";

export type Metric = "share_of_voice" | "mention_rate" | "avg_position" | "sentiment";

export type Row = { day: string } & Record<string, number | string | null>;

/** Long -> wide: one row per day, one column per brand id, for a line chart. */
export function pivotSeries(series: Overview["series"], metric: Metric): Row[] {
  const byDay = new Map<string, Row>();
  for (const point of series) {
    let row = byDay.get(point.day);
    if (!row) {
      row = { day: point.day };
      byDay.set(point.day, row);
    }
    row[point.brand_id] = point[metric];
  }
  return [...byDay.values()].sort((a, b) => a.day.localeCompare(b.day));
}

/** Rows for the per-model grouped bar chart: one row per model, one column per brand. */
export function pivotByModel(byModel: Overview["by_model"], metric: Exclude<Metric, "share_of_voice">): Row[] {
  const rows = new Map<string, Row>();
  for (const m of byModel) {
    let row = rows.get(m.model_key);
    if (!row) {
      row = { day: m.model_key };
      rows.set(m.model_key, row);
    }
    row[m.brand_id] = m[metric];
  }
  return [...rows.values()];
}
