import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const validFixture = path.resolve("../tests/fixtures/step31_orders.csv");
const duplicateFixture = path.resolve("../tests/fixtures/step31_duplicate_orders.csv");
const browserPrincipal = "step29-browser-reviewer";

async function importAndStart(page: Page, fixture: string, project: string) {
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
  await page.locator("#source-file").setInputFiles(fixture);
  await page.getByRole("button", { name: "Import and register" }).click();
  await expect(page.getByRole("status")).toContainText("Source imported");
  await page.locator("#project-id").fill(project);
  await page.getByRole("button", { name: "Create run and start discovery" }).click();
  await expect(page).toHaveURL(/\/runs\//);
}

async function waitForJobsToSettle(page: Page) {
  const runId = new URL(page.url()).pathname.split("/").pop();
  if (!runId) throw new Error("run id was not present in the browser URL");
  await expect.poll(async () => {
    const response = await page.request.get(`${new URL(page.url()).origin}/api/v1/runs/${runId}/jobs`, {
      headers: { "X-Local-Principal": browserPrincipal },
    });
    if (!response.ok()) return false;
    const body = await response.json() as { items?: Array<{ status?: string }> };
    return (body.items ?? []).every((item) => !["QUEUED", "RUNNING", "RETRY_WAIT"].includes(item.status ?? ""));
  }, { timeout: 120_000, intervals: [250, 500, 1000, 2000] }).toBe(true);
}

test("Step31 browser failure path keeps duplicate identity non-successful", async ({ page }) => {
  await importAndStart(page, duplicateFixture, `step31-browser-failure-${Date.now()}`);
  await expect(page.getByTestId("run-status")).toHaveText("FAILED", { timeout: 240_000 });
  await waitForJobsToSettle(page);
  await expect(page.getByText("Output is not yet consumable")).toBeVisible();
  await expect(page.getByText("Validated OLAP output available")).toHaveCount(0);
  await expect(page.getByText("DEPENDENCY_DISCOVERY").first()).toBeVisible();
});

test("Step31 browser review state survives reload and remains actionable", async ({ page }) => {
  await importAndStart(page, validFixture, `step31-browser-review-${Date.now()}`);
  const checkpoint = page.locator(".checkpoint").first();
  await expect(checkpoint).toBeVisible({ timeout: 240_000 });
  const checkpointText = await checkpoint.innerText();
  await expect(page.getByRole("button", { name: /Accept and resume/ })).toBeVisible();
  await page.reload();
  await expect(page.locator(".checkpoint").first()).toHaveText(checkpointText);
  await expect(page.getByRole("button", { name: /Accept and resume/ })).toBeVisible();
  await page.getByRole("button", { name: /Accept and resume/ }).click();
  await expect(page.locator(".checkpoint").first()).not.toHaveText(checkpointText, { timeout: 180_000 });
});
