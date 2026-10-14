/**
 * Runtime schemas for every API payload the UI reads.
 *
 * Each schema is checked at compile time against the types generated from the
 * backend's OpenAPI spec (`schema.d.ts`, via `npm run gen:api`): if the API
 * changes shape, `tsc` fails here before anything ships. At runtime, responses
 * are parsed, so a contract drift surfaces as a clear error instead of `undefined`
 * deep inside a chart.
 */
import { z } from "zod";

import type { components } from "./schema";

type S = components["schemas"];

const uuid = z.string();
const isoDate = z.string();
const decimal = z.string(); // Decimals travel as strings to keep precision.

export const brandRole = z.enum(["primary", "competitor"]);

export const tokenOut = z.object({
  access_token: z.string(),
  token_type: z.literal("bearer"),
  expires_in: z.number(),
}) satisfies z.ZodType<S["TokenOut"]>;

export const workspaceOut = z.object({
  id: uuid,
  name: z.string(),
  models: z.array(z.string()),
  schedule_enabled: z.boolean(),
  daily_run_hour_utc: z.number(),
  created_at: z.string(),
}) satisfies z.ZodType<S["WorkspaceOut"]>;

export const meOut = z.object({
  id: uuid,
  email: z.string(),
  workspaces: z.array(workspaceOut),
}) satisfies z.ZodType<S["MeOut"]>;

export const brandOut = z.object({
  id: uuid,
  name: z.string(),
  role: brandRole,
  aliases: z.array(z.string()),
  domains: z.array(z.string()),
  color: z.string().nullable(),
  created_at: z.string(),
}) satisfies z.ZodType<S["BrandOut"]>;

export const promptOut = z.object({
  id: uuid,
  text: z.string(),
  tags: z.array(z.string()),
  is_active: z.boolean(),
  created_at: z.string(),
  responses_7d: z.number(),
  mention_rate_7d: z.number().nullish(),
}) satisfies z.ZodType<S["PromptOut"]>;

export const promptImportResult = z.object({
  created: z.number(),
  skipped_duplicates: z.number(),
  errors: z.array(z.string()),
}) satisfies z.ZodType<S["PromptImportResult"]>;

export const runOut = z.object({
  id: uuid,
  trigger: z.string(),
  status: z.string(),
  scheduled_for: isoDate.nullable(),
  models: z.array(z.string()),
  total_tasks: z.number(),
  input_tokens: z.number(),
  output_tokens: z.number(),
  cost_usd: decimal,
  created_at: z.string(),
  started_at: z.string().nullable(),
  finished_at: z.string().nullable(),
}) satisfies z.ZodType<S["RunOut"]>;

export const providerCost = z.object({
  model_key: z.string(),
  responses: z.number(),
  input_tokens: z.number(),
  output_tokens: z.number(),
  cost_usd: decimal,
}) satisfies z.ZodType<S["ProviderCost"]>;

export const runDetailOut = runOut.extend({
  counts: z.record(z.string(), z.number()),
  analyzed: z.number(),
  percent: z.number(),
  by_model: z.array(providerCost),
}) satisfies z.ZodType<S["RunDetailOut"]>;

export const taskOut = z.object({
  id: z.number(),
  prompt_id: uuid,
  prompt_text: z.string(),
  model_key: z.string(),
  status: z.string(),
  attempts: z.number(),
  last_error: z.string().nullable(),
  updated_at: z.string(),
}) satisfies z.ZodType<S["TaskOut"]>;

export const brandRef = z.object({
  id: uuid,
  name: z.string(),
  role: brandRole,
  color: z.string().nullable(),
}) satisfies z.ZodType<S["BrandRef"]>;

export const brandKpi = z.object({
  brand_id: uuid,
  share_of_voice: z.number(),
  mention_rate: z.number(),
  avg_position: z.number().nullable(),
  sentiment: z.number().nullable(),
  top1_rate: z.number(),
  share_of_voice_delta: z.number().nullish(),
  mention_rate_delta: z.number().nullish(),
}) satisfies z.ZodType<S["BrandKpi"]>;

export const seriesPoint = z.object({
  day: isoDate,
  brand_id: uuid,
  share_of_voice: z.number(),
  mention_rate: z.number(),
  avg_position: z.number().nullable(),
  sentiment: z.number().nullable(),
}) satisfies z.ZodType<S["SeriesPoint"]>;

export const modelBreakdown = z.object({
  model_key: z.string(),
  brand_id: uuid,
  responses: z.number(),
  mention_rate: z.number(),
  avg_position: z.number().nullable(),
  sentiment: z.number().nullable(),
}) satisfies z.ZodType<S["ModelBreakdown"]>;

export const overviewOut = z.object({
  date_from: isoDate,
  date_to: isoDate,
  responses: z.number(),
  cost_usd: decimal,
  brands: z.array(brandRef),
  kpis: z.array(brandKpi),
  series: z.array(seriesPoint),
  by_model: z.array(modelBreakdown),
}) satisfies z.ZodType<S["OverviewOut"]>;

export const sourceRow = z.object({
  domain: z.string(),
  citations: z.number(),
  responses_citing: z.number(),
  citation_share: z.number(),
  is_brand_domain: z.boolean(),
}) satisfies z.ZodType<S["SourceRow"]>;

export const mentionSpan = z.object({
  start: z.number(),
  end: z.number(),
  kind: z.string(),
}) satisfies z.ZodType<S["MentionSpan"]>;

export const responseMention = z.object({
  brand_id: uuid,
  brand: z.string(),
  position: z.number(),
  sentiment: z.string(),
  score: z.number(),
  spans: z.array(mentionSpan),
}) satisfies z.ZodType<S["ResponseMention"]>;

export const responseSource = z.object({
  url: z.string(),
  domain: z.string(),
  rank: z.number(),
  origin: z.string(),
}) satisfies z.ZodType<S["ResponseSource"]>;

export const promptResponseOut = z.object({
  id: z.number(),
  run_id: uuid,
  day: isoDate,
  model_key: z.string(),
  model_name: z.string(),
  text: z.string(),
  latency_ms: z.number(),
  cost_usd: decimal,
  analyzed: z.boolean(),
  overall_sentiment: z.string().nullable(),
  mentions: z.array(responseMention),
  sources: z.array(responseSource),
}) satisfies z.ZodType<S["PromptResponseOut"]>;

export const page = <T extends z.ZodType>(item: T) =>
  z.object({ items: z.array(item), total: z.number(), limit: z.number(), offset: z.number() });

export const cursorPage = <T extends z.ZodType>(item: T) =>
  z.object({ items: z.array(item), next_cursor: z.string().nullable() });

export type Workspace = z.infer<typeof workspaceOut>;
export type Brand = z.infer<typeof brandOut>;
export type Prompt = z.infer<typeof promptOut>;
export type Run = z.infer<typeof runOut>;
export type RunDetail = z.infer<typeof runDetailOut>;
export type Task = z.infer<typeof taskOut>;
export type Overview = z.infer<typeof overviewOut>;
export type BrandKpi = z.infer<typeof brandKpi>;
export type SourceRow = z.infer<typeof sourceRow>;
export type PromptResponse = z.infer<typeof promptResponseOut>;
export type ResponseMention = z.infer<typeof responseMention>;
