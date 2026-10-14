"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { useSession } from "@/lib/session";

import { api } from "./client";
import * as s from "./schemas";

export interface DateRange {
  from: string;
  to: string;
}

const qs = (params: Record<string, string | number | boolean | null | undefined>) => {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  const str = sp.toString();
  return str ? `?${str}` : "";
};

function useWs() {
  const { workspace } = useSession();
  return workspace ? `/workspaces/${workspace.id}` : null;
}

// ------------------------------------------------------------------ dashboard

export function useOverview(range: DateRange, model?: string) {
  const ws = useWs();
  return useQuery({
    queryKey: ["overview", ws, range, model],
    queryFn: () =>
      api(`${ws}/dashboard/overview${qs({ date_from: range.from, date_to: range.to, model })}`, s.overviewOut),
    enabled: !!ws,
    placeholderData: keepPreviousData,
  });
}

export function useSources(range: DateRange, offset: number, limit = 25) {
  const ws = useWs();
  return useQuery({
    queryKey: ["sources", ws, range, offset, limit],
    queryFn: () =>
      api(
        `${ws}/dashboard/sources${qs({ date_from: range.from, date_to: range.to, offset, limit })}`,
        s.page(s.sourceRow),
      ),
    enabled: !!ws,
    placeholderData: keepPreviousData,
  });
}

// ------------------------------------------------------------------ brands

export function useBrands() {
  const ws = useWs();
  return useQuery({
    queryKey: ["brands", ws],
    queryFn: () => api(`${ws}/brands?limit=200`, s.page(s.brandOut)),
    enabled: !!ws,
  });
}

export interface BrandInput {
  name: string;
  role: "primary" | "competitor";
  aliases: string[];
  domains: string[];
}

export function useBrandMutations() {
  const ws = useWs();
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["brands", ws] });
  return {
    create: useMutation({
      mutationFn: (body: BrandInput) => api(`${ws}/brands`, s.brandOut, { method: "POST", body }),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: string) => api(`${ws}/brands/${id}`, z.undefined(), { method: "DELETE" }),
      onSuccess: invalidate,
    }),
  };
}

// ------------------------------------------------------------------ prompts

export function usePrompts(q: string, offset: number, limit = 25) {
  const ws = useWs();
  return useQuery({
    queryKey: ["prompts", ws, q, offset, limit],
    queryFn: () => api(`${ws}/prompts${qs({ q, offset, limit })}`, s.page(s.promptOut)),
    enabled: !!ws,
    placeholderData: keepPreviousData,
  });
}

export function usePrompt(id: string) {
  const ws = useWs();
  return useQuery({
    queryKey: ["prompt", ws, id],
    queryFn: () => api(`${ws}/prompts/${id}`, s.promptOut),
    enabled: !!ws,
  });
}

export function usePromptResponses(id: string, model: string | undefined, cursor: string | null) {
  const ws = useWs();
  return useQuery({
    queryKey: ["prompt-responses", ws, id, model, cursor],
    queryFn: () =>
      api(`${ws}/prompts/${id}/responses${qs({ model, cursor, limit: 12 })}`, s.cursorPage(s.promptResponseOut)),
    enabled: !!ws,
    placeholderData: keepPreviousData,
  });
}

export function usePromptMutations() {
  const ws = useWs();
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["prompts", ws] });
  return {
    create: useMutation({
      mutationFn: (text: string) => api(`${ws}/prompts`, s.promptOut, { method: "POST", body: { text } }),
      onSuccess: invalidate,
    }),
    importCsv: useMutation({
      mutationFn: (file: File) => {
        const form = new FormData();
        form.append("file", file);
        return api(`${ws}/prompts/import`, s.promptImportResult, { method: "POST", form });
      },
      onSuccess: invalidate,
    }),
    toggle: useMutation({
      mutationFn: (p: { id: string; is_active: boolean }) =>
        api(`${ws}/prompts/${p.id}`, s.promptOut, { method: "PATCH", body: { is_active: p.is_active } }),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: string) => api(`${ws}/prompts/${id}`, z.undefined(), { method: "DELETE" }),
      onSuccess: invalidate,
    }),
  };
}

// ------------------------------------------------------------------ runs

const ACTIVE = new Set(["pending", "running"]);

export function useRuns(offset = 0, limit = 20) {
  const ws = useWs();
  return useQuery({
    queryKey: ["runs", ws, offset, limit],
    queryFn: () => api(`${ws}/runs${qs({ offset, limit })}`, s.page(s.runOut)),
    enabled: !!ws,
    // Keep polling while any visible run is still in flight.
    refetchInterval: (query) => (query.state.data?.items.some((r) => ACTIVE.has(r.status)) ? 2000 : false),
  });
}

export function useRun(id: string) {
  const ws = useWs();
  return useQuery({
    queryKey: ["run", ws, id],
    queryFn: () => api(`${ws}/runs/${id}`, s.runDetailOut),
    enabled: !!ws,
    refetchInterval: (query) => (query.state.data && !ACTIVE.has(query.state.data.status) ? false : 1500),
  });
}

export function useRunTasks(id: string, status: string | undefined, cursor: string | null) {
  const ws = useWs();
  return useQuery({
    queryKey: ["run-tasks", ws, id, status, cursor],
    queryFn: () => api(`${ws}/runs/${id}/tasks${qs({ status, cursor, limit: 25 })}`, s.cursorPage(s.taskOut)),
    enabled: !!ws,
    placeholderData: keepPreviousData,
  });
}

export function useRunMutations() {
  const ws = useWs();
  const qc = useQueryClient();
  return {
    trigger: useMutation({
      mutationFn: () => api(`${ws}/runs`, s.runOut, { method: "POST" }),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["runs", ws] }),
    }),
    retryFailed: useMutation({
      mutationFn: (id: string) =>
        api(`${ws}/runs/${id}/retry-failed`, z.object({ requeued: z.number() }), { method: "POST" }),
      onSuccess: (_d, id) => qc.invalidateQueries({ queryKey: ["run", ws, id] }),
    }),
  };
}
