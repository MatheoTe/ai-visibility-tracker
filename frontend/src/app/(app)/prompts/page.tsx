"use client";

import { Trash2, Upload } from "lucide-react";
import Link from "next/link";
import { useRef, useState, type FormEvent } from "react";

import { PageHeader } from "@/components/app-shell";
import { Pager } from "@/components/pager";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { TBody, THead, Table, Td, Th, Tr } from "@/components/ui/table";
import { DEMO_MODE, DEMO_READ_ONLY_MESSAGE } from "@/lib/api/demo";
import { usePromptMutations, usePrompts } from "@/lib/api/hooks";
import { pct } from "@/lib/format";
import { showToast } from "@/lib/toast";

const LIMIT = 25;

export default function PromptsPage() {
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [text, setText] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const { data } = usePrompts(q, offset, LIMIT);
  const { create, importCsv, toggle, remove } = usePromptMutations();

  function onAdd(e: FormEvent) {
    e.preventDefault();
    create.mutate(text.trim(), { onSuccess: () => setText("") });
  }

  function onImport(file: File | undefined) {
    if (!file) return;
    importCsv.mutate(file, {
      onSuccess: (r) =>
        setNotice(
          `Imported ${r.created} prompt${r.created === 1 ? "" : "s"}, skipped ${r.skipped_duplicates} duplicate${
            r.skipped_duplicates === 1 ? "" : "s"
          }${r.errors.length ? `, ${r.errors.length} invalid row(s)` : ""}.`,
        ),
      onSettled: () => {
        if (fileRef.current) fileRef.current.value = "";
      },
    });
  }

  return (
    <>
      <PageHeader
        title="Prompts"
        description="The questions asked to every model on every run - phrase them the way your buyers would."
        actions={
          <>
            <input
              ref={fileRef}
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              data-testid="csv-input"
              onChange={(e) => onImport(e.target.files?.[0])}
            />
            <Button
              variant="outline"
              // In the live demo, say so before the file picker opens rather than after a file is chosen.
              onClick={() => (DEMO_MODE ? showToast(DEMO_READ_ONLY_MESSAGE) : fileRef.current?.click())}
              disabled={importCsv.isPending}
            >
              <Upload /> Import CSV
            </Button>
          </>
        }
      />
      <Card>
        <CardContent className="space-y-4 pt-5">
          <form onSubmit={onAdd} className="flex flex-col gap-2 sm:flex-row" aria-label="Add prompt">
            <Input
              placeholder='e.g. "best CRM for small businesses"'
              value={text}
              onChange={(e) => setText(e.target.value)}
              aria-label="New prompt"
              minLength={3}
              required
            />
            <Button type="submit" disabled={create.isPending}>
              Add prompt
            </Button>
          </form>
          {create.error ? <p className="text-sm text-destructive">{create.error.message}</p> : null}
          {notice ? (
            <p className="text-sm text-muted-foreground" role="status">
              {notice}
            </p>
          ) : null}
          <Input
            placeholder="Search prompts…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setOffset(0);
            }}
            aria-label="Search prompts"
            className="max-w-xs"
          />
          <Table>
            <THead>
              <Tr>
                <Th>Prompt</Th>
                <Th>Tags</Th>
                <Th className="text-right">Answers (7d)</Th>
                <Th className="text-right">You mentioned (7d)</Th>
                <Th>Active</Th>
                <Th className="w-10" />
              </Tr>
            </THead>
            <TBody>
              {data?.items.map((p) => (
                <Tr key={p.id}>
                  <Td className="max-w-md">
                    <Link href={`/prompts/${p.id}`} className="font-medium hover:underline">
                      {p.text}
                    </Link>
                  </Td>
                  <Td>
                    <div className="flex flex-wrap gap-1">
                      {p.tags.map((t) => (
                        <Badge key={t} variant="outline">
                          {t}
                        </Badge>
                      ))}
                    </div>
                  </Td>
                  <Td className="tabular text-right">{p.responses_7d ?? 0}</Td>
                  <Td className="tabular text-right">{pct(p.mention_rate_7d, 0)}</Td>
                  <Td>
                    <input
                      type="checkbox"
                      checked={p.is_active}
                      aria-label={`Active: ${p.text}`}
                      onChange={(e) => toggle.mutate({ id: p.id, is_active: e.target.checked })}
                    />
                  </Td>
                  <Td>
                    <Button variant="ghost" size="icon" aria-label={`Delete prompt ${p.text}`} onClick={() => remove.mutate(p.id)}>
                      <Trash2 />
                    </Button>
                  </Td>
                </Tr>
              ))}
              {data && data.items.length === 0 ? (
                <Tr>
                  <Td colSpan={6} className="py-8 text-center text-muted-foreground">
                    No prompts yet. Add one above or import a CSV with a <code>prompt</code> column.
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
