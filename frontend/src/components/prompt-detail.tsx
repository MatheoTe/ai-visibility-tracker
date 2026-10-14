"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { HighlightedText } from "@/components/highlighted-text";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useBrands, usePrompt, usePromptResponses } from "@/lib/api/hooks";
import { brandColors } from "@/lib/colors";
import { modelLabel, sentiment, usd } from "@/lib/format";
import { useSession } from "@/lib/session";

const sentimentVariant = (s: string | null) =>
  s === "positive" ? "good" : s === "negative" ? "critical" : s === "mixed" ? "warning" : "default";

export function PromptDetail({ promptId }: { promptId: string }) {
  const { workspace } = useSession();
  const [model, setModel] = useState("");
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const cursor = cursors[cursors.length - 1];
  const prompt = usePrompt(promptId);
  const brands = useBrands();
  const responses = usePromptResponses(promptId, model || undefined, cursor);
  const colors = useMemo(() => brandColors(brands.data?.items ?? []), [brands.data]);

  return (
    <>
      <Link href="/prompts" className="mb-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3" /> Prompts
      </Link>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{prompt.data ? `“${prompt.data.text}”` : <Skeleton className="h-7 w-80" />}</h1>
          <p className="mt-1 text-sm text-muted-foreground">Every answer, newest first, with detected mentions highlighted.</p>
        </div>
        <Select
          aria-label="Filter by model"
          value={model}
          onChange={(e) => {
            setModel(e.target.value);
            setCursors([null]);
          }}
        >
          <option value="">All models</option>
          {workspace?.models.map((m) => (
            <option key={m} value={m}>
              {modelLabel(m)}
            </option>
          ))}
        </Select>
      </div>

      <div className="space-y-4">
        {responses.isPending ? <Skeleton className="h-48" /> : null}
        {responses.data?.items.map((r) => (
          <Card key={r.id} data-testid="response-card">
            <CardHeader className="flex-row flex-wrap items-center gap-2 space-y-0 pb-2">
              <Badge variant="outline">{r.day}</Badge>
              <Badge variant="info">{modelLabel(r.model_key)}</Badge>
              {r.overall_sentiment ? <Badge variant={sentimentVariant(r.overall_sentiment)}>{r.overall_sentiment}</Badge> : null}
              {!r.analyzed ? <Badge variant="warning">analysis pending</Badge> : null}
              <span className="ml-auto text-xs text-muted-foreground tabular">
                {r.latency_ms} ms · {usd(r.cost_usd)}
              </span>
            </CardHeader>
            <CardContent className="space-y-3">
              {r.mentions.length ? (
                <div className="flex flex-wrap gap-2 text-xs">
                  {r.mentions.map((m) => (
                    <span key={m.brand_id} className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1">
                      <span className="size-2 rounded-full" style={{ background: colors.get(m.brand_id) }} aria-hidden />
                      <span className="font-medium">#{m.position}</span> {m.brand}
                      <span className="text-muted-foreground">sentiment {sentiment(m.score)}</span>
                    </span>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">No tracked brand mentioned.</p>
              )}
              <HighlightedText text={r.text} mentions={r.mentions} colors={colors} />
              {r.sources.length ? (
                <div className="border-t border-border pt-2 text-xs">
                  <span className="text-muted-foreground">Sources: </span>
                  {r.sources.map((s, i) => (
                    <span key={s.url}>
                      {i > 0 ? " · " : null}
                      <a href={s.url} target="_blank" rel="noreferrer" className="hover:underline">
                        {s.domain}
                      </a>
                    </span>
                  ))}
                </div>
              ) : null}
            </CardContent>
          </Card>
        ))}
        {responses.data && responses.data.items.length === 0 ? (
          <Card className="p-8 text-center text-sm text-muted-foreground">No answers yet - start a run.</Card>
        ) : null}
        <div className="flex gap-2">
          {cursors.length > 1 ? (
            <Button variant="outline" size="sm" onClick={() => setCursors(cursors.slice(0, -1))}>
              Newer
            </Button>
          ) : null}
          {responses.data?.next_cursor ? (
            <Button variant="outline" size="sm" onClick={() => setCursors([...cursors, responses.data.next_cursor])}>
              Older
            </Button>
          ) : null}
        </div>
      </div>
    </>
  );
}
