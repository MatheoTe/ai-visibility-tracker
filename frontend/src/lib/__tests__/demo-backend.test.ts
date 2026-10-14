import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { describe, expect, it, vi } from "vitest";

import {
  createDemoBackend,
  dayOffset,
  DemoReadOnlyError,
  rangeKeyFor,
  shiftDates,
  type DemoSnapshot,
} from "@/lib/api/demo";
import { ApiError } from "@/lib/api/errors";
import {
  brandOut,
  cursorPage,
  meOut,
  overviewOut,
  page,
  promptOut,
  promptResponseOut,
  runDetailOut,
  runOut,
  sourceRow,
  taskOut,
} from "@/lib/api/schemas";
import { rangeFor, type RangeKey } from "@/lib/dates";

const WS = "ws-1";
const P1 = "p-1";
const RUN = "run-1";
const MODELS = ["anthropic:claude-sonnet-5-5", "openai:gpt-5-mini"];

const overview = (from: string, to: string, responses: number) => ({
  date_from: from,
  date_to: to,
  responses,
  cost_usd: "0.010000",
  brands: [{ id: "b-1", name: "Lumora CRM", role: "primary" as const, color: null }],
  kpis: [],
  series: [{ day: to, brand_id: "b-1", share_of_voice: 0.4, mention_rate: 0.6, avg_position: 1.5, sentiment: 0.2 }],
  by_model: [],
});

const prompt = (id: string, text: string, is_active = true) => ({
  id,
  text,
  tags: [],
  is_active,
  created_at: "2026-10-01T09:00:00Z",
  responses_7d: 28,
  mention_rate_7d: 0.5,
});

const run = {
  id: RUN,
  trigger: "backfill",
  status: "succeeded",
  scheduled_for: "2026-10-13",
  models: MODELS,
  total_tasks: 4,
  input_tokens: 10,
  output_tokens: 20,
  cost_usd: "0.001000",
  created_at: "2026-10-13T06:00:00Z",
  started_at: "2026-10-13T06:00:01Z",
  finished_at: "2026-10-13T06:00:09Z",
};

const snapshot: DemoSnapshot = {
  version: 1,
  as_of: "2026-10-14",
  generated_at: "2026-10-14T12:00:00Z",
  me: {
    id: "u-1",
    email: "demo@avt.dev",
    workspaces: [
      { id: WS, name: "Demo", models: MODELS, schedule_enabled: true, daily_run_hour_utc: 6, created_at: "2026-09-14T00:00:00Z" },
    ],
  },
  brands: [
    { id: "b-1", name: "Lumora CRM", role: "primary", aliases: [], domains: [], color: null, created_at: "2026-09-14T00:00:00Z" },
  ],
  prompts: [
    prompt(P1, "best CRM for small businesses"),
    prompt("p-2", "Which CRM has the best free plan?", false),
    prompt("p-3", "best CRM for nonprofits"),
  ],
  runs: [run],
  overview: {
    "7d": { "": overview("2026-10-08", "2026-10-14", 700), [MODELS[0]]: overview("2026-10-08", "2026-10-14", 175) },
    "30d": { "": overview("2026-09-15", "2026-10-14", 3000) },
  },
  sources: {
    "30d": Array.from({ length: 30 }, (_, i) => ({
      domain: `site${i}.com`,
      citations: 30 - i,
      responses_citing: 30 - i,
      citation_share: (30 - i) / 465,
      is_brand_domain: i === 0,
    })),
  },
};

const responses = Array.from({ length: 6 }, (_, i) => ({
  id: 100 - i,
  run_id: RUN,
  day: "2026-10-13",
  model_key: MODELS[i % 2],
  model_name: "x",
  text: `answer ${i}`,
  latency_ms: 10,
  cost_usd: "0.000100",
  analyzed: true,
  overall_sentiment: "positive",
  mentions: [],
  sources: [],
}));

const tasks = Array.from({ length: 4 }, (_, i) => ({
  id: i + 1,
  prompt_id: P1,
  prompt_text: "best CRM for small businesses",
  model_key: MODELS[i % 2],
  status: i === 3 ? "dead" : "succeeded",
  attempts: i === 3 ? 5 : 1,
  last_error: i === 3 ? "upstream 503" : null,
  updated_at: "2026-10-13T06:00:05Z",
}));

const files: Record<string, unknown> = {
  "snapshot.json": snapshot,
  [`prompts/${P1}.json`]: { responses },
  [`runs/${RUN}.json`]: { detail: { ...run, counts: { succeeded: 3, dead: 1 }, analyzed: 3, percent: 100, by_model: [] }, tasks },
};

function backend(today = new Date("2026-10-14T15:00:00Z")) {
  const load = vi.fn(async (file: string) => {
    if (!(file in files)) throw new Error(`missing ${file}`);
    return structuredClone(files[file]);
  });
  return { load, demo: createDemoBackend(load, () => today) };
}

const ws = `/workspaces/${WS}`;

describe("demo backend", () => {
  it("serves the session and validates against the API schemas", async () => {
    const { demo } = backend();
    const me = meOut.parse(await demo.request("/auth/me"));
    expect(me.workspaces[0].id).toBe(WS);
  });

  it("picks the overview by date range and model", async () => {
    const { demo } = backend();
    const all = overviewOut.parse(await demo.request(`${ws}/dashboard/overview?date_from=2026-09-15&date_to=2026-10-14`));
    expect(all.responses).toBe(3000);
    const week = overviewOut.parse(
      await demo.request(`${ws}/dashboard/overview?date_from=2026-10-08&date_to=2026-10-14&model=${MODELS[0]}`),
    );
    expect(week.responses).toBe(175);
    await expect(
      demo.request(`${ws}/dashboard/overview?date_from=2026-10-08&date_to=2026-10-14&model=${MODELS[1]}`),
    ).rejects.toMatchObject({ status: 404 });
  });

  it("paginates sources like the API", async () => {
    const { demo } = backend();
    const p = page(sourceRow).parse(
      await demo.request(`${ws}/dashboard/sources?date_from=2026-09-15&date_to=2026-10-14&offset=25&limit=25`),
    );
    expect(p.total).toBe(30);
    expect(p.items.map((s) => s.domain)).toEqual(["site25.com", "site26.com", "site27.com", "site28.com", "site29.com"]);
  });

  it("searches and filters prompts", async () => {
    const { demo } = backend();
    const found = page(promptOut).parse(await demo.request(`${ws}/prompts?q=NONPROFIT&offset=0&limit=25`));
    expect(found.items.map((p) => p.id)).toEqual(["p-3"]);
    const inactive = page(promptOut).parse(await demo.request(`${ws}/prompts?active=false`));
    expect(inactive.items.map((p) => p.id)).toEqual(["p-2"]);
    expect(promptOut.parse(await demo.request(`${ws}/prompts/${P1}`)).text).toBe("best CRM for small businesses");
  });

  it("cursor-pages prompt answers, filtered by model, loading the file once", async () => {
    const { demo, load } = backend();
    const first = cursorPage(promptResponseOut).parse(await demo.request(`${ws}/prompts/${P1}/responses?limit=2`));
    expect(first.items.map((r) => r.id)).toEqual([100, 99]);
    expect(first.next_cursor).toBe("2");
    const last = cursorPage(promptResponseOut).parse(
      await demo.request(`${ws}/prompts/${P1}/responses?limit=2&cursor=${first.next_cursor}&model=${encodeURIComponent(MODELS[1])}`),
    );
    expect(last.items.map((r) => r.id)).toEqual([95]);
    expect(last.next_cursor).toBeNull();
    expect(load.mock.calls.filter(([f]) => f === `prompts/${P1}.json`)).toHaveLength(1);
  });

  it("serves run detail and the dead-letter view with keyset cursors", async () => {
    const { demo } = backend();
    const detail = runDetailOut.parse(await demo.request(`${ws}/runs/${RUN}`));
    expect(detail.counts.dead).toBe(1);
    const dead = cursorPage(taskOut).parse(await demo.request(`${ws}/runs/${RUN}/tasks?status=dead&limit=25`));
    expect(dead.items.map((t) => t.last_error)).toEqual(["upstream 503"]);
    const p1 = cursorPage(taskOut).parse(await demo.request(`${ws}/runs/${RUN}/tasks?limit=3`));
    expect(p1.next_cursor).toBe("3");
    const p2 = cursorPage(taskOut).parse(await demo.request(`${ws}/runs/${RUN}/tasks?limit=3&cursor=3`));
    expect(p2.items.map((t) => t.id)).toEqual([4]);
  });

  it("refuses every write except signing in", async () => {
    const { demo } = backend();
    await expect(demo.request(`${ws}/runs`, "POST")).rejects.toBeInstanceOf(DemoReadOnlyError);
    await expect(demo.request(`${ws}/brands/b-1`, "DELETE")).rejects.toBeInstanceOf(DemoReadOnlyError);
    await expect(demo.request(`${ws}/prompts/${P1}`, "PATCH")).rejects.toBeInstanceOf(DemoReadOnlyError);
    await expect(demo.request("/auth/register", "POST")).rejects.toBeInstanceOf(DemoReadOnlyError);
    await expect(demo.request("/auth/login", "POST")).resolves.toMatchObject({ access_token: "demo" });
    expect(new DemoReadOnlyError()).toBeInstanceOf(ApiError);
  });

  it("answers 404 for unknown workspaces, ids and paths", async () => {
    const { demo } = backend();
    await expect(demo.request("/workspaces/other/brands")).rejects.toMatchObject({ status: 404 });
    await expect(demo.request(`${ws}/runs/nope`)).rejects.toMatchObject({ status: 404 });
    await expect(demo.request(`${ws}/prompts/nope/responses`)).rejects.toMatchObject({ status: 404 });
    await expect(demo.request(`${ws}/reanalyze`)).rejects.toMatchObject({ status: 404 });
  });

  it("moves every date so the snapshot ends on the viewer's today", async () => {
    const { demo } = backend(new Date("2026-11-03T01:00:00Z")); // 20 days after as_of
    const ov = overviewOut.parse(await demo.request(`${ws}/dashboard/overview?date_from=2026-10-05&date_to=2026-11-03`));
    expect([ov.date_from, ov.date_to]).toEqual(["2026-10-05", "2026-11-03"]);
    expect(ov.series[0].day).toBe("2026-11-03");
    const r = runDetailOut.parse(await demo.request(`${ws}/runs/${RUN}`));
    expect(r.scheduled_for).toBe("2026-11-02");
    expect(r.created_at).toBe("2026-11-02T06:00:00.000Z");
    const answers = cursorPage(promptResponseOut).parse(await demo.request(`${ws}/prompts/${P1}/responses`));
    expect(answers.items[0].day).toBe("2026-11-02");
  });

  it("retries the snapshot after a failed load", async () => {
    let fail = true;
    const demo = createDemoBackend(async (file) => {
      if (fail) throw new Error("offline");
      return structuredClone(files[file]);
    });
    await expect(demo.request("/auth/me")).rejects.toThrow("offline");
    fail = false;
    await expect(demo.request("/auth/me")).resolves.toMatchObject({ email: "demo@avt.dev" });
  });
});

describe("demo date helpers", () => {
  it("computes whole-day offsets in UTC", () => {
    expect(dayOffset("2026-10-14", new Date("2026-10-14T23:59:00Z"))).toBe(0);
    expect(dayOffset("2026-10-14", new Date("2026-10-16T00:00:00Z"))).toBe(2);
  });

  it("shifts only ISO dates and datetimes", () => {
    const out = shiftDates({ day: "2026-10-31", at: "2026-10-31T23:30:00Z", text: "2026-10-31 is in the text", n: 3 }, 1);
    expect(out).toEqual({ day: "2026-11-01", at: "2026-11-01T23:30:00.000Z", text: "2026-10-31 is in the text", n: 3 });
  });

  it("maps a requested window to the nearest exported range", () => {
    expect(rangeKeyFor("2026-10-08", "2026-10-14")).toBe("7d");
    expect(rangeKeyFor("2026-09-15", "2026-10-14")).toBe("30d");
    expect(rangeKeyFor("2026-07-17", "2026-10-14")).toBe("90d");
    expect(rangeKeyFor(null, null)).toBe("30d");
  });
});

describe("committed demo snapshot", () => {
  // The live demo ships public/demo/*.json; if the API contract drifts, regenerate it
  // with scripts/export-demo-snapshot.mjs instead of letting the demo break at runtime.
  const read = async (file: string) => JSON.parse(await readFile(join(process.cwd(), "public/demo", file), "utf8"));

  it("matches the API contract for every page the demo serves", async () => {
    const committed = createDemoBackend(read, () => new Date());
    const me = meOut.parse(await committed.request("/auth/me"));
    const w = `/workspaces/${me.workspaces[0].id}`;
    const runs = page(runOut).parse(await committed.request(`${w}/runs?offset=0&limit=100`));
    expect(runs.total).toBeGreaterThanOrEqual(30);
    const prompts = page(promptOut).parse(await committed.request(`${w}/prompts?limit=200`));
    expect(prompts.total).toBeGreaterThan(0);
    page(brandOut).parse(await committed.request(`${w}/brands?limit=200`));
    for (const key of ["7d", "30d", "90d"]) {
      const { from, to } = rangeFor(key as RangeKey);
      overviewOut.parse(await committed.request(`${w}/dashboard/overview?date_from=${from}&date_to=${to}`));
      for (const m of me.workspaces[0].models) {
        overviewOut.parse(await committed.request(`${w}/dashboard/overview?date_from=${from}&date_to=${to}&model=${m}`));
      }
      page(sourceRow).parse(await committed.request(`${w}/dashboard/sources?date_from=${from}&date_to=${to}`));
    }
    const answers = cursorPage(promptResponseOut).parse(
      await committed.request(`${w}/prompts/${prompts.items[0].id}/responses?limit=12`),
    );
    expect(answers.items.length).toBeGreaterThan(0);
    for (const r of runs.items) runDetailOut.parse(await committed.request(`${w}/runs/${r.id}`));
    cursorPage(taskOut).parse(await committed.request(`${w}/runs/${runs.items[0].id}/tasks?limit=25`));
  });
});
