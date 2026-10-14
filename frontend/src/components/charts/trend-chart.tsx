"use client";

import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { Overview } from "@/lib/api/schemas";
import { shortDay } from "@/lib/format";
import { pivotSeries, type Metric } from "@/lib/series";

interface Props {
  overview: Overview;
  metric: Metric;
  colors: Map<string, string>;
  format: (v: number) => string;
  height?: number;
}

const AXIS = { fontSize: 11, fill: "var(--muted-foreground)" };

export function TrendChart({ overview, metric, colors, format, height = 280 }: Props) {
  const data = pivotSeries(overview.series, metric);
  const reversed = metric === "avg_position"; // rank 1 belongs at the top
  return (
    <div style={{ height }} role="img" aria-label={`${metric.replaceAll("_", " ")} per brand over time`}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="day" tickFormatter={shortDay} tick={AXIS} tickLine={false} axisLine={false} minTickGap={24} />
          <YAxis
            tickFormatter={(v: number) => format(v)}
            tick={AXIS}
            tickLine={false}
            axisLine={false}
            width={52}
            reversed={reversed}
            domain={metric === "sentiment" ? [-1, 1] : ["auto", "auto"]}
          />
          <Tooltip
            contentStyle={{
              background: "var(--card)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              fontSize: 12,
              color: "var(--foreground)",
            }}
            labelFormatter={(label) => String(label)}
            formatter={(value, name) => [
              typeof value === "number" ? format(value) : "—",
              overview.brands.find((b) => b.id === name)?.name ?? String(name),
            ]}
          />
          <Legend
            formatter={(value: string) => (
              <span style={{ color: "var(--foreground)", fontSize: 12 }}>
                {overview.brands.find((b) => b.id === value)?.name ?? value}
              </span>
            )}
          />
          {overview.brands.map((b) => (
            <Line
              key={b.id}
              dataKey={b.id}
              name={b.id}
              stroke={colors.get(b.id)}
              strokeWidth={b.role === "primary" ? 2.5 : 2}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--card)" }}
              connectNulls
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
