"use client";

import { useMemo, useState } from "react";

import { PageHeader } from "@/components/app-shell";
import { Pager } from "@/components/pager";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { TBody, THead, Table, Td, Th, Tr } from "@/components/ui/table";
import { useSources } from "@/lib/api/hooks";
import { RANGES, rangeFor, type RangeKey } from "@/lib/dates";
import { int, pct } from "@/lib/format";

const LIMIT = 25;

export default function SourcesPage() {
  const [rangeKey, setRangeKey] = useState<RangeKey>("30d");
  const [offset, setOffset] = useState(0);
  const range = useMemo(() => rangeFor(rangeKey), [rangeKey]);
  const { data } = useSources(range, offset, LIMIT);
  const top = data?.items[0]?.citation_share ?? 0;

  return (
    <>
      <PageHeader
        title="Sources"
        description="The domains AI answers cite. Getting covered here is how you move the numbers."
        actions={
          <Select
            aria-label="Date range"
            value={rangeKey}
            onChange={(e) => {
              setRangeKey(e.target.value as RangeKey);
              setOffset(0);
            }}
          >
            {RANGES.map((r) => (
              <option key={r.key} value={r.key}>
                Last {r.label}
              </option>
            ))}
          </Select>
        }
      />
      <Card>
        <CardContent className="pt-5">
          <Table>
            <THead>
              <Tr>
                <Th className="w-10">#</Th>
                <Th>Domain</Th>
                <Th className="w-1/3">Share of citations</Th>
                <Th className="text-right">Citations</Th>
                <Th className="text-right">Answers citing</Th>
              </Tr>
            </THead>
            <TBody>
              {data?.items.map((s, i) => (
                <Tr key={s.domain}>
                  <Td className="tabular text-muted-foreground">{offset + i + 1}</Td>
                  <Td>
                    <span className="font-medium">{s.domain}</span>
                    {s.is_brand_domain ? (
                      <Badge variant="info" className="ml-2">
                        tracked brand
                      </Badge>
                    ) : null}
                  </Td>
                  <Td>
                    <div className="flex items-center gap-2">
                      <div className="h-2 flex-1 rounded-full bg-muted">
                        <div
                          className="h-2 rounded-full bg-primary"
                          style={{ width: `${top ? (s.citation_share / top) * 100 : 0}%` }}
                        />
                      </div>
                      <span className="tabular w-12 text-right text-xs">{pct(s.citation_share)}</span>
                    </div>
                  </Td>
                  <Td className="tabular text-right">{int(s.citations)}</Td>
                  <Td className="tabular text-right">{int(s.responses_citing)}</Td>
                </Tr>
              ))}
              {data && data.items.length === 0 ? (
                <Tr>
                  <Td colSpan={5} className="py-8 text-center text-muted-foreground">
                    No cited sources in this range.
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
