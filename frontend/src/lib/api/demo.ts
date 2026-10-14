/**
 * Read-only stand-in for the backend, used by the static live demo
 * (`NEXT_PUBLIC_DEMO_MODE=1`).
 *
 * It answers the same `/api/v1/...` paths the real API serves, from a JSON
 * snapshot exported from a seeded backend (`scripts/export-demo-snapshot.mjs`)
 * and committed under `public/demo/`. Filtering and pagination that the backend
 * does in SQL (prompt search, model filter, task status, offsets and cursors) are
 * reproduced here over the exported rows; aggregates (overview, sources) are
 * served exactly as the backend computed them for each date range.
 *
 * Dates are shifted so the snapshot's `as_of` day becomes the viewer's today:
 * the demo always shows "the last 30 days" instead of ageing.
 */
import { ApiError } from "./errors";
import type { Brand, Overview, Prompt, PromptResponse, Run, RunDetail, SourceRow, Task } from "./schemas";
import type { meOut } from "./schemas";
import type { z } from "zod";

export const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE === "1" || process.env.NEXT_PUBLIC_DEMO_MODE === "true";
export const DEMO_REPO_URL = "https://github.com/MatheoTe/echolytics";
export const DEMO_READ_ONLY_MESSAGE =
  "Disabled in the live demo — run it locally or in Codespaces to start runs and edit data.";

/** A mutating request reached the demo backend. */
export class DemoReadOnlyError extends ApiError {
  constructor() {
    super(403, DEMO_READ_ONLY_MESSAGE);
    this.name = "DemoReadOnlyError";
  }
}

export interface DemoSnapshot {
  version: number;
  as_of: string;
  generated_at: string;
  me: z.infer<typeof meOut>;
  brands: Brand[];
  prompts: Prompt[];
  runs: Run[];
  /** range key ("7d" | "30d" | "90d") -> model key ("" = all models) -> overview */
  overview: Record<string, Record<string, Overview>>;
  /** range key -> full sources leaderboard */
  sources: Record<string, SourceRow[]>;
}

export interface DemoPromptFile {
  responses: PromptResponse[];
}

export interface DemoRunFile {
  detail: RunDetail;
  tasks: Task[];
}

/** Fetches a file below `/demo/` and returns its parsed JSON. */
export type DemoLoader = (file: string) => Promise<unknown>;

const DAY_MS = 86_400_000;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const DATETIME_RE = /^\d{4}-\d{2}-\d{2}T/;

const utcDay = (d: Date) => Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());

/** Whole days from the snapshot's `as_of` to `today` (UTC). */
export function dayOffset(asOf: string, today: Date): number {
  return Math.round((utcDay(today) - Date.parse(`${asOf}T00:00:00Z`)) / DAY_MS);
}

/** Return a copy of `value` with every ISO date / datetime string moved by `days`. */
export function shiftDates<T>(value: T, days: number): T {
  if (days === 0) return value;
  const walk = (v: unknown): unknown => {
    if (typeof v === "string") {
      if (DATE_RE.test(v)) return new Date(Date.parse(`${v}T00:00:00Z`) + days * DAY_MS).toISOString().slice(0, 10);
      if (DATETIME_RE.test(v)) {
        const t = Date.parse(v);
        return Number.isNaN(t) ? v : new Date(t + days * DAY_MS).toISOString();
      }
      return v;
    }
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, walk(x)]));
    return v;
  };
  return walk(value) as T;
}

const RANGE_DAYS: Record<string, number> = { "7d": 7, "30d": 30, "90d": 90 };

/** Map a requested date window onto the closest range the snapshot was exported for. */
export function rangeKeyFor(from: string | null, to: string | null): string {
  if (!from || !to) return "30d";
  const days = Math.round((Date.parse(to) - Date.parse(from)) / DAY_MS) + 1;
  let best = "30d";
  for (const [key, n] of Object.entries(RANGE_DAYS)) {
    if (Math.abs(n - days) < Math.abs(RANGE_DAYS[best] - days)) best = key;
  }
  return best;
}

const int = (v: string | null, fallback: number) => {
  const n = v === null ? NaN : Number(v);
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : fallback;
};

function page<T>(items: T[], params: URLSearchParams, defaultLimit: number) {
  const limit = int(params.get("limit"), defaultLimit);
  const offset = int(params.get("offset"), 0);
  return { items: items.slice(offset, offset + limit), total: items.length, limit, offset };
}

/** Cursor pages over an in-memory list; the cursor is the offset of the next item. */
function cursorPage<T>(items: T[], params: URLSearchParams, defaultLimit: number) {
  const limit = int(params.get("limit"), defaultLimit);
  const start = int(params.get("cursor"), 0);
  const end = start + limit;
  return { items: items.slice(start, end), next_cursor: end < items.length ? String(end) : null };
}

const notFound = () => new ApiError(404, "Not found");

export interface DemoBackend {
  request(path: string, method?: string): Promise<unknown>;
}

export function createDemoBackend(load: DemoLoader, today: () => Date = () => new Date()): DemoBackend {
  let snapshot: Promise<{ data: DemoSnapshot; shift: number }> | null = null;
  const files = new Map<string, Promise<unknown>>();

  const getSnapshot = () => {
    snapshot ??= (load("snapshot.json") as Promise<DemoSnapshot>).then((raw) => {
      const shift = dayOffset(raw.as_of, today());
      return { data: shiftDates(raw, shift), shift };
    });
    snapshot.catch(() => (snapshot = null)); // a failed load is retried on the next request
    return snapshot;
  };

  const getFile = async <T>(file: string): Promise<T> => {
    const { shift } = await getSnapshot();
    let pending = files.get(file);
    if (!pending) {
      pending = load(file).then((raw) => shiftDates(raw, shift));
      pending.catch(() => files.delete(file));
      files.set(file, pending);
    }
    try {
      return (await pending) as T;
    } catch {
      throw notFound();
    }
  };

  async function request(path: string, method = "GET"): Promise<unknown> {
    const url = new URL(path, "http://demo.invalid");
    const params = url.searchParams;
    const parts = url.pathname.split("/").filter(Boolean);

    if (method.toUpperCase() !== "GET") {
      // Signing in is the one write the demo accepts: it hands back a placeholder token.
      if (url.pathname === "/auth/login") return { access_token: "demo", token_type: "bearer", expires_in: 86_400 };
      throw new DemoReadOnlyError();
    }

    const { data } = await getSnapshot();
    if (url.pathname === "/auth/me") return data.me;

    // Everything else is /workspaces/{id}/...
    if (parts[0] !== "workspaces" || !data.me.workspaces.some((w) => w.id === parts[1])) throw notFound();
    const [resource, id, sub] = parts.slice(2);

    if (resource === "dashboard" && id === "overview") {
      const byModel = data.overview[rangeKeyFor(params.get("date_from"), params.get("date_to"))];
      const result = byModel?.[params.get("model") ?? ""];
      if (!result) throw notFound();
      return result;
    }
    if (resource === "dashboard" && id === "sources") {
      return page(data.sources[rangeKeyFor(params.get("date_from"), params.get("date_to"))] ?? [], params, 25);
    }
    if (resource === "brands" && !id) return page(data.brands, params, 100);

    if (resource === "prompts") {
      if (!id) {
        const q = params.get("q")?.trim().toLowerCase();
        const active = params.get("active");
        const rows = data.prompts.filter(
          (p) =>
            (!q || p.text.toLowerCase().includes(q)) && (active === null || String(p.is_active) === active),
        );
        return page(rows, params, 50);
      }
      const prompt = data.prompts.find((p) => p.id === id);
      if (!prompt) throw notFound();
      if (!sub) return prompt;
      if (sub === "responses") {
        const { responses } = await getFile<DemoPromptFile>(`prompts/${id}.json`);
        const model = params.get("model");
        return cursorPage(model ? responses.filter((r) => r.model_key === model) : responses, params, 20);
      }
      throw notFound();
    }

    if (resource === "runs") {
      if (!id) return page(data.runs, params, 20);
      if (!data.runs.some((r) => r.id === id)) throw notFound();
      const run = await getFile<DemoRunFile>(`runs/${id}.json`);
      if (!sub) return run.detail;
      if (sub === "tasks") {
        const status = params.get("status");
        const limit = int(params.get("limit"), 50);
        const after = int(params.get("cursor"), 0);
        // Same keyset as the API: ascending task id, cursor = last id of the page.
        const rows = run.tasks.filter((t) => (!status || t.status === status) && t.id > after);
        const items = rows.slice(0, limit);
        return { items, next_cursor: rows.length > limit ? String(items[items.length - 1].id) : null };
      }
      throw notFound();
    }

    throw notFound();
  }

  return { request };
}

/** Loader for the browser: files are served statically from `/demo/`. */
export const fetchDemoFile: DemoLoader = async (file) => {
  const res = await fetch(`/demo/${file}`);
  if (!res.ok) throw new ApiError(res.status, `Demo data missing: ${file}`);
  return res.json();
};
