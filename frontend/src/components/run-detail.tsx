"use client";

import { ArrowLeft, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ProgressBar, RunStatus } from "@/components/run-status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { TBody, THead, Table, Td, Th, Tr } from "@/components/ui/table";
import { useRun, useRunMutations, useRunTasks } from "@/lib/api/hooks";
import { int, modelLabel, usd } from "@/lib/format";
import { cn } from "@/lib/utils";

const FILTERS = ["all", "dead", "queued", "running", "succeeded"] as const;

export function RunDetail({ runId }: { runId: string }) {
  const { data: run } = useRun(runId);
  const { retryFailed } = useRunMutations();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const tasks = useRunTasks(runId, filter === "all" ? undefined : filter, cursors[cursors.length - 1]);

  if (!run) return <Skeleton className="h-64 w-full" />;
  const dead = run.counts.dead ?? 0;
  const succeeded = run.counts.succeeded ?? 0;

  return (
    <>
      <Link href="/runs" className="mb-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3" /> Runs
      </Link>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold tracking-tight">Run</h1>
          <RunStatus status={run.status} />
          <span className="font-mono text-xs text-muted-foreground">{run.id.slice(0, 8)}</span>
        </div>
        {dead > 0 ? (
          <Button variant="outline" onClick={() => retryFailed.mutate(run.id)} disabled={retryFailed.isPending}>
            <RotateCcw /> Retry {dead} failed
          </Button>
        ) : null}
      </div>

      <div className="grid gap-4 md:grid-cols-4">
        <Card className="p-4 md:col-span-2">
          <div className="mb-2 flex justify-between text-xs text-muted-foreground">
            <span>Progress</span>
            <span className="tabular" data-testid="run-progress">
              {int(succeeded + dead)} / {int(run.total_tasks)} · {run.percent}%
            </span>
          </div>
          <ProgressBar value={run.percent} label="Run progress" />
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            {Object.entries(run.counts).map(([k, v]) => (
              <span key={k} className="tabular">
                {k}: <span className="text-foreground">{int(v)}</span>
              </span>
            ))}
            <span className="tabular">
              analysed: <span className="text-foreground">{int(run.analyzed)}</span>
            </span>
          </div>
        </Card>
        <Card className="p-4">
          <div className="text-xs text-muted-foreground">Tokens</div>
          <div className="tabular mt-1 text-2xl font-semibold">{int(run.input_tokens + run.output_tokens)}</div>
          <div className="tabular text-xs text-muted-foreground">
            {int(run.input_tokens)} in · {int(run.output_tokens)} out
          </div>
        </Card>
        <Card className="p-4">
          <div className="text-xs text-muted-foreground">Estimated cost</div>
          <div className="tabular mt-1 text-2xl font-semibold">{usd(run.cost_usd)}</div>
          <div className="text-xs text-muted-foreground">{run.models.length} models</div>
        </Card>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_2fr]">
        <Card>
          <CardHeader>
            <CardTitle>Cost by model</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <THead>
                <Tr>
                  <Th>Model</Th>
                  <Th className="text-right">Answers</Th>
                  <Th className="text-right">Tokens</Th>
                  <Th className="text-right">Cost</Th>
                </Tr>
              </THead>
              <TBody>
                {run.by_model.map((m) => (
                  <Tr key={m.model_key}>
                    <Td>{modelLabel(m.model_key)}</Td>
                    <Td className="tabular text-right">{int(m.responses)}</Td>
                    <Td className="tabular text-right">{int(m.input_tokens + m.output_tokens)}</Td>
                    <Td className="tabular text-right">{usd(m.cost_usd)}</Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
            <CardTitle>Tasks</CardTitle>
            <div className="flex flex-wrap gap-1" role="tablist" aria-label="Task status">
              {FILTERS.map((f) => (
                <button
                  key={f}
                  type="button"
                  role="tab"
                  aria-selected={filter === f}
                  onClick={() => {
                    setFilter(f);
                    setCursors([null]);
                  }}
                  className={cn(
                    "rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted",
                    filter === f && "bg-muted font-medium text-foreground",
                  )}
                >
                  {f === "dead" ? "dead-letter" : f}
                </button>
              ))}
            </div>
          </CardHeader>
          <CardContent>
            <Table>
              <THead>
                <Tr>
                  <Th>Prompt</Th>
                  <Th>Model</Th>
                  <Th>Status</Th>
                  <Th className="text-right">Attempts</Th>
                </Tr>
              </THead>
              <TBody>
                {tasks.data?.items.map((t) => (
                  <Tr key={t.id}>
                    <Td className="max-w-xs truncate" title={t.last_error ?? t.prompt_text}>
                      {t.prompt_text}
                      {t.last_error ? <div className="truncate text-xs text-destructive">{t.last_error}</div> : null}
                    </Td>
                    <Td className="text-muted-foreground">{modelLabel(t.model_key)}</Td>
                    <Td>
                      <RunStatus status={t.status} />
                    </Td>
                    <Td className="tabular text-right">{t.attempts}</Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
            <div className="flex gap-2 pt-3">
              {cursors.length > 1 ? (
                <Button variant="outline" size="sm" onClick={() => setCursors(cursors.slice(0, -1))}>
                  Previous
                </Button>
              ) : null}
              {tasks.data?.next_cursor ? (
                <Button variant="outline" size="sm" onClick={() => setCursors([...cursors, tasks.data.next_cursor])}>
                  Next
                </Button>
              ) : null}
            </div>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
