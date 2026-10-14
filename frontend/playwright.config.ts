import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end: real Next.js app + real FastAPI + real arq workers (fake LLM
 * providers) against Postgres and Redis. Point E2E_DATABASE_URL / E2E_REDIS_URL at
 * disposable services (CI uses job services; locally `docker compose up postgres redis`).
 */
const DB = process.env.E2E_DATABASE_URL ?? "postgresql+asyncpg://avt:avt@localhost:5432/avt_e2e";
const REDIS = process.env.E2E_REDIS_URL ?? "redis://localhost:6379/14";
const API_PORT = Number(process.env.E2E_API_PORT ?? 8100);
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3100);

const backendEnv = {
  AVT_DATABASE_URL: DB,
  AVT_REDIS_URL: REDIS,
  AVT_FORCE_FAKE_PROVIDERS: "true",
  AVT_FAKE_LATENCY_MS_MIN: "5",
  AVT_FAKE_LATENCY_MS_MAX: "30",
  AVT_LOG_LEVEL: "WARNING",
  AVT_CORS_ORIGINS: `["http://localhost:${WEB_PORT}"]`,
  AVT_JWT_SECRET: "e2e-secret-e2e-secret-e2e-secret-e2e",
};

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `uv run alembic upgrade head && uv run uvicorn avt.api.main:app --port ${API_PORT}`,
      cwd: "../backend",
      url: `http://localhost:${API_PORT}/readyz`,
      env: backendEnv,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: "uv run arq avt.worker.main.WorkerSettings",
      cwd: "../backend",
      url: "http://localhost:9101/metrics",
      env: { ...backendEnv, AVT_WORKER_METRICS_PORT: "9101" },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: "uv run arq avt.worker.main.AnalysisWorkerSettings",
      cwd: "../backend",
      url: "http://localhost:9102/metrics",
      env: { ...backendEnv, AVT_WORKER_METRICS_PORT: "9102" },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: `npm run build && npx next start -p ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}/login`,
      env: { NEXT_PUBLIC_API_URL: `http://localhost:${API_PORT}` },
      reuseExistingServer: !process.env.CI,
      timeout: 300_000,
    },
  ],
});
