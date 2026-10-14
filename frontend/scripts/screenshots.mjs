// Capture README screenshots from a running stack seeded with `avt-seed`.
//   BASE_URL=http://localhost:3000 node scripts/screenshots.mjs
import { chromium } from "@playwright/test";

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const OUT = new URL("../../docs/screenshots/", import.meta.url).pathname;

const browser = await chromium.launch();
for (const theme of ["light", "dark"]) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: theme, deviceScaleFactor: 2 });
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByText("Competitive snapshot").waitFor();
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}dashboard-${theme}.png`, fullPage: true });
  if (theme === "dark") continue;

  await page.getByRole("link", { name: "Prompts" }).click();
  await page.getByRole("link", { name: "best CRM for small businesses" }).waitFor();
  await page.screenshot({ path: `${OUT}prompts.png` });
  await page.getByRole("link", { name: "best CRM for small businesses" }).click();
  await page.getByTestId("response-card").first().waitFor();
  await page.screenshot({ path: `${OUT}prompt-drilldown.png` });

  await page.getByRole("link", { name: "Sources" }).click();
  await page.getByRole("cell").first().waitFor();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}sources.png` });

  await page.getByRole("link", { name: "Runs" }).click();
  await page.locator('a[href^="/runs/"]').first().click();
  await page.getByTestId("run-progress").waitFor();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}run-detail.png` });
}
await browser.close();
console.log(`screenshots written to ${OUT}`);
