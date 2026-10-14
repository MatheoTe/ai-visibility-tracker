import { expect, test } from "@playwright/test";

/**
 * The core loop a new user goes through: create a brand and competitors, add
 * prompts, trigger a run, watch it complete, and read the results.
 */
test("create brand -> add prompts -> run -> see results", async ({ page }) => {
  const email = `e2e-${Date.now()}@example.com`;

  await page.goto("/login");
  await page.getByRole("button", { name: "No account? Create one" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("e2e-password-123");
  await page.getByLabel("Workspace name").fill("E2E Workspace");
  await page.getByRole("button", { name: "Create account" }).click();

  // Brands: our brand + two competitors.
  await expect(page.getByRole("heading", { name: "Brands" })).toBeVisible();
  const brandForm = page.getByRole("form", { name: "Add brand" });
  for (const [name, aliases] of [
    ["Acme CRM", "Acme"],
    ["Pipewise", ""],
    ["Clientra", ""],
  ]) {
    await brandForm.getByLabel("Name").fill(name);
    await brandForm.getByLabel("Aliases (comma separated)").fill(aliases);
    await brandForm.getByRole("button", { name: "Add brand" }).click();
    await expect(page.getByRole("cell", { name, exact: true })).toBeVisible();
  }
  await expect(page.getByRole("cell").getByText("primary", { exact: true })).toBeVisible();

  // Prompts.
  await page.getByRole("link", { name: "Prompts" }).click();
  for (const prompt of ["best CRM for small businesses", "which CRM has the best free plan?"]) {
    await page.getByLabel("New prompt").fill(prompt);
    await page.getByRole("button", { name: "Add prompt" }).click();
    await expect(page.getByRole("link", { name: prompt })).toBeVisible();
  }

  // Run and watch it finish (fake providers, so it takes seconds).
  await page.getByRole("link", { name: "Runs" }).click();
  await page.getByRole("button", { name: "Run now" }).click();
  await expect(page.getByText("completed")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("run-progress")).toContainText("100%");

  // Dashboard shows numbers for our brand and the competitors.
  await page.getByRole("link", { name: "Overview" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByText("Competitive snapshot")).toBeVisible();
  const snapshot = page.locator("table").first();
  await expect(snapshot.getByText("Acme CRM")).toBeVisible();
  await expect(snapshot.getByText("Pipewise")).toBeVisible();

  // Drill into a prompt: answers from every model, with mentions highlighted.
  await page.getByRole("link", { name: "Prompts" }).click();
  await page.getByRole("link", { name: "best CRM for small businesses" }).click();
  await expect(page.getByTestId("response-card").first()).toBeVisible();
  await expect(page.getByTestId("response-card")).toHaveCount(4); // one answer per default model
  await expect(page.locator("mark").first()).toBeVisible();
});
