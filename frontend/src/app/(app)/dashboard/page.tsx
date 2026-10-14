"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { PageHeader } from "@/components/app-shell";
import { ModelBars } from "@/components/charts/model-bars";
import { TrendChart } from "@/components/charts/trend-chart";
import { KpiCard } from "@/components/kpi-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { TBody, THead, Table, Td, Th, Tr } from "@/components/ui/table";
import { useOverview } from "@/lib/api/hooks";
import { brandColors } from "@/lib/colors";
import { RANGES, rangeFor, type RangeKey } from "@/lib/dates";
import * as f from "@/lib/format";
import type { Metric } from "@/lib/series";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

const METRICS: { key: Metric; label: string; format: (v: number) => string }[] = [
  { key: "share_of_voice", label: "Share of voice", format: (v) => f.pct(v, 0) },
  { key: "mention_rate", label: "Mention rate", format: (v) => f.pct(v, 0) },
  { key: "avg_position", label: "Avg. position", format: (v) => `#${v.toFixed(1)}` },
  { key: "sentiment", label: "Sentiment", format: (v) => v.toFixed(2) },
];

const trendOf = (v: number | null | undefined) =>
  v === null || v === undefined ? null : v > 0.0005 ? "up" : v < -0.0005 ? "down" : "flat";

export default function DashboardPage() {
  const { workspace } = useSession();
  const [rangeKey, setRangeKey] = useState<RangeKey>("30d");
  const [model, setModel] = useState<string>("");
  const [metric, setMetric] = useState<Metric>("share_of_voice");
  const range = useMemo(() => rangeFor(rangeKey), [rangeKey]);
  const { data, isPending, error } = useOverview(range, model || undefined);
  const colors = useMemo(() => brandColors(data?.brands ?? []), [data?.brands]);

  const primary = data?.brands.find((b) => b.role === "primary");
  const primaryKpi = data?.kpis.find((k) => k.brand_id === primary?.id);
  const active = METRICS.find((m) => m.key === metric) ?? METRICS[0];

  return (
    <>
      <PageHeader
        title="Overview"
        description={
          primary ? `How AI assistants talk about ${primary.name} versus its competitors.` : "Brand visibility in AI answers."
        }
        actions={
          <>
            <Select aria-label="Model" value={model} onChange={(e) => setModel(e.target.value)}>
              <option value="">All models</option>
              {workspace?.models.map((m) => (
                <option key={m} value={m}>
                  {f.modelLabel(m)}
                </option>
              ))}
            </Select>
            <div className="flex rounded-md border border-border p-0.5" role="group" aria-label="Date range">
              {RANGES.map((r) => (
                <button
                  key={r.key}
                  type="button"
                  onClick={() => setRangeKey(r.key)}
                  aria-pressed={rangeKey === r.key}
                  className={cn(
                    "rounded px-2.5 py-1 text-xs text-muted-foreground",
                    rangeKey === r.key && "bg-muted font-medium text-foreground",
                  )}
                >
                  {r.label}
                </button>
              ))}
            </div>
          </>
        }
      />

      {error ? <p className="text-sm text-destructive">Could not load the dashboard: {error.message}</p> : null}
      {isPending ? (
        <div className="grid gap-4 md:grid-cols-4">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
          <Skeleton className="h-80 md:col-span-4" />
        </div>
      ) : data && data.responses === 0 ? (
        <Card className="p-8 text-center">
          <p className="font-medium">No results in this range yet.</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Add your brand, competitors and prompts, then start a run - results land here as soon as it finishes.
          </p>
          <div className="mt-4 flex justify-center gap-2">
            <Button asChild variant="outline">
              <Link href="/brands">Brands</Link>
            </Button>
            <Button asChild>
              <Link href="/runs">Start a run</Link>
            </Button>
          </div>
        </Card>
      ) : data ? (
        <div className="space-y-6">
          <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" aria-label={`${primary?.name ?? "Brand"} KPIs`}>
            <KpiCard
              label="Share of voice"
              value={f.pct(primaryKpi?.share_of_voice)}
              delta={f.ppDelta(primaryKpi?.share_of_voice_delta)}
              trend={trendOf(primaryKpi?.share_of_voice_delta)}
              hint="vs previous period"
            />
            <KpiCard
              label="Mention rate"
              value={f.pct(primaryKpi?.mention_rate)}
              delta={f.ppDelta(primaryKpi?.mention_rate_delta)}
              trend={trendOf(primaryKpi?.mention_rate_delta)}
              hint="of answers mention you"
            />
            <KpiCard label="Average position" value={f.position(primaryKpi?.avg_position)} hint="when mentioned (1 = first)" />
            <KpiCard
              label="Sentiment"
              value={f.sentiment(primaryKpi?.sentiment)}
              hint={`${f.int(data.responses)} answers · ${f.usd(data.cost_usd)}`}
            />
          </section>

          <Card>
            <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
              <div>
                <CardTitle>{active.label} over time</CardTitle>
                <CardDescription>Daily, {model ? f.modelLabel(model) : "all models"}</CardDescription>
              </div>
              <div className="flex flex-wrap gap-1" role="tablist" aria-label="Metric">
                {METRICS.map((m) => (
                  <button
                    key={m.key}
                    role="tab"
                    type="button"
                    aria-selected={metric === m.key}
                    onClick={() => setMetric(m.key)}
                    className={cn(
                      "rounded-md px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted",
                      metric === m.key && "bg-muted font-medium text-foreground",
                    )}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
            </CardHeader>
            <CardContent>
              <TrendChart overview={data} metric={metric} colors={colors} format={active.format} />
            </CardContent>
          </Card>

          <div className="grid gap-6 xl:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Competitive snapshot</CardTitle>
                <CardDescription>
                  {data.date_from} → {data.date_to}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <Table>
                  <THead>
                    <Tr>
                      <Th>Brand</Th>
                      <Th className="text-right">Share of voice</Th>
                      <Th className="text-right">Mention rate</Th>
                      <Th className="text-right">Avg. pos.</Th>
                      <Th className="text-right">Ranked #1</Th>
                      <Th className="text-right">Sentiment</Th>
                    </Tr>
                  </THead>
                  <TBody>
                    {data.brands.map((b) => {
                      const k = data.kpis.find((x) => x.brand_id === b.id);
                      return (
                        <Tr key={b.id}>
                          <Td>
                            <span className="inline-flex items-center gap-2">
                              <span className="size-2.5 rounded-full" style={{ background: colors.get(b.id) }} aria-hidden />
                              <span className={b.role === "primary" ? "font-semibold" : undefined}>{b.name}</span>
                            </span>
                          </Td>
                          <Td className="tabular text-right">
                            {f.pct(k?.share_of_voice)}
                            {k?.share_of_voice_delta !== undefined && k?.share_of_voice_delta !== null ? (
                              <span className="ml-1 text-xs text-muted-foreground">{f.ppDelta(k.share_of_voice_delta)}</span>
                            ) : null}
                          </Td>
                          <Td className="tabular text-right">{f.pct(k?.mention_rate)}</Td>
                          <Td className="tabular text-right">{f.position(k?.avg_position)}</Td>
                          <Td className="tabular text-right">{f.pct(k?.top1_rate)}</Td>
                          <Td className="tabular text-right">{f.sentiment(k?.sentiment)}</Td>
                        </Tr>
                      );
                    })}
                  </TBody>
                </Table>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Mention rate by model</CardTitle>
                <CardDescription>Share of each model&apos;s answers that mention the brand</CardDescription>
              </CardHeader>
              <CardContent>
                <ModelBars overview={data} colors={colors} />
              </CardContent>
            </Card>
          </div>
        </div>
      ) : null}
    </>
  );
}
