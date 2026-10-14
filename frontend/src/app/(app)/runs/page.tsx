"use client";

import { Play } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { PageHeader } from "@/components/app-shell";
import { Pager } from "@/components/pager";
import { RunStatus } from "@/components/run-status";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { TBody, THead, Table, Td, Th, Tr } from "@/components/ui/table";
import { useRunMutations, useRuns } from "@/lib/api/hooks";
import { int, relativeTime, usd } from "@/lib/format";

const LIMIT = 20;

export default function RunsPage() {
  const [offset, setOffset] = useState(0);
  const { data } = useRuns(offset, LIMIT);
  const { trigger } = useRunMutations();
  const router = useRouter();

  return (
    <>
      <PageHeader
        title="Runs"
        description="Each run asks every active prompt to every model. One runs automatically every day."
        actions={
          <Button onClick={() => trigger.mutate(undefined, { onSuccess: (run) => router.push(`/runs/${run.id}`) })} disabled={trigger.isPending}>
            <Play /> Run now
          </Button>
        }
      />
      {trigger.error ? <p className="mb-3 text-sm text-destructive">{trigger.error.message}</p> : null}
      <Card>
        <CardContent className="pt-5">
          <Table>
            <THead>
              <Tr>
                <Th>Started</Th>
                <Th>Trigger</Th>
                <Th>Status</Th>
                <Th className="text-right">Tasks</Th>
                <Th className="text-right">Tokens</Th>
                <Th className="text-right">Est. cost</Th>
              </Tr>
            </THead>
            <TBody>
              {data?.items.map((r) => (
                <Tr key={r.id}>
                  <Td>
                    <Link href={`/runs/${r.id}`} className="font-medium hover:underline">
                      {r.scheduled_for && r.trigger !== "manual" ? r.scheduled_for : relativeTime(r.created_at)}
                    </Link>
                  </Td>
                  <Td className="text-muted-foreground">{r.trigger}</Td>
                  <Td>
                    <RunStatus status={r.status} />
                  </Td>
                  <Td className="tabular text-right">{int(r.total_tasks)}</Td>
                  <Td className="tabular text-right">{int(r.input_tokens + r.output_tokens)}</Td>
                  <Td className="tabular text-right">{usd(r.cost_usd)}</Td>
                </Tr>
              ))}
              {data && data.items.length === 0 ? (
                <Tr>
                  <Td colSpan={6} className="py-8 text-center text-muted-foreground">
                    No runs yet.
                  </Td>
                </Tr>
              ) : null}
            </TBody>
          </Table>
          {data ? <Pager offset={offset} limit={LIMIT} total={data.total} onChange={setOffset} /> : null}
        </CardContent>
      </Card>
    </>
  );
}
