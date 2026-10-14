"use client";

import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { Overview } from "@/lib/api/schemas";
import { modelLabel, pct } from "@/lib/format";
import { pivotByModel } from "@/lib/series";

const AXIS = { fontSize: 11, fill: "var(--muted-foreground)" };

export function ModelBars({ overview, colors }: { overview: Overview; colors: Map<string, string> }) {
  const data = pivotByModel(overview.by_model, "mention_rate");
  return (
    <div style={{ height: 280 }} role="img" aria-label="Mention rate per model and brand">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }} barGap={2} barCategoryGap="24%">
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="day" tickFormatter={modelLabel} tick={AXIS} tickLine={false} axisLine={false} />
          <YAxis tickFormatter={(v: number) => pct(v, 0)} tick={AXIS} tickLine={false} axisLine={false} width={44} />
          <Tooltip
            cursor={{ fill: "var(--muted)", opacity: 0.5 }}
            contentStyle={{
              background: "var(--card)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              fontSize: 12,
              color: "var(--foreground)",
            }}
            labelFormatter={(label) => modelLabel(String(label))}
            formatter={(value, name) => [
              typeof value === "number" ? pct(value) : "—",
              overview.brands.find((b) => b.id === name)?.name ?? String(name),
            ]}
          />
          <Legend
            itemSorter={null}
            formatter={(value: string) => (
              <span style={{ color: "var(--foreground)", fontSize: 12 }}>
                {overview.brands.find((b) => b.id === value)?.name ?? value}
              </span>
            )}
          />
          {overview.brands.map((b) => (
            <Bar key={b.id} dataKey={b.id} fill={colors.get(b.id)} radius={[4, 4, 0, 0]} isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
