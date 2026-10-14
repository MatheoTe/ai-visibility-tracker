import { describe, expect, it } from "vitest";

import { overviewOut, page, promptResponseOut, runDetailOut } from "@/lib/api/schemas";

describe("API schemas", () => {
  it("parses a run detail payload", () => {
    const run = runDetailOut.parse({
      id: "6f1c",
      trigger: "manual",
      status: "running",
      scheduled_for: null,
      models: ["anthropic:claude-sonnet-5-5"],
      total_tasks: 10,
      input_tokens: 100,
      output_tokens: 200,
      cost_usd: "0.002100",
      created_at: "2026-10-14T10:00:00Z",
      started_at: null,
      finished_at: null,
      counts: { succeeded: 4, queued: 6 },
      analyzed: 3,
      percent: 40,
      by_model: [],
    });
    expect(run.counts.succeeded).toBe(4);
  });

  it("rejects payloads that drifted from the contract", () => {
    expect(() => overviewOut.parse({ responses: "12" })).toThrow();
    expect(() => page(promptResponseOut).parse({ items: [{}], total: 1, limit: 1, offset: 0 })).toThrow();
  });
});
