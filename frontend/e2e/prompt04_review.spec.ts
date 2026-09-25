import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const validFixture = path.resolve("../tests/fixtures/step31_orders.csv");

async function importAndStart(page: Page) {
  const seededRunId = process.env.PROMPT04_REVIEW_RUN_ID;
  if (seededRunId) {
    await page.goto(`/runs/${encodeURIComponent(seededRunId)}`);
    return;
  }
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
  await page.locator("#source-file").setInputFiles(validFixture);
  await page.getByRole("button", { name: "Import and register" }).click();
  await expect(page.getByRole("status")).toContainText("Source imported");
  await page.locator("#project-id").fill(`prompt04-browser-${Date.now()}`);
  await page.getByRole("button", { name: "Create run and start discovery" }).click();
  await expect(page).toHaveURL(/\/runs\//);
}

test("Prompt04 browser review actions are durable and server-owned", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await importAndStart(page);

  const review = page.locator(".review-object").first();
  await expect(review).toBeVisible({ timeout: 240_000 });
  const checkpointText = await review.locator(".checkpoint").innerText();
  for (const action of ["ACCEPT", "REJECT", "OVERRIDE", "LABEL", "LOCK", "DEFER"]) {
    await expect(review.getByRole("button", { name: action, exact: true })).toBeVisible();
  }
  await expect(review).toContainText("Confidence semantics");
  await expect(review).toContainText("Provenance");

  await review.getByRole("button", { name: "LABEL", exact: true }).click();
  await expect(review.getByLabel("Label namespace")).toBeVisible();
  await review.getByRole("button", { name: "Save LABEL", exact: true }).click();
  await expect(page.locator(".notice.success")).toContainText("Action saved: LABEL");
  await expect(page.getByTestId("review-history")).toContainText("LABEL");

  await page.reload();
  const reloadedReview = page.locator(".review-object").first();
  await expect(reloadedReview).toContainText(checkpointText);
  await expect(page.getByTestId("review-history")).toContainText("LABEL");
  await reloadedReview.getByRole("button", { name: "Accept and resume when eligible", exact: true }).click();
  await expect(page.getByText("Run resumed by the durable execution boundary.")).toBeVisible();
  await expect(reloadedReview).toContainText(checkpointText);
  expect(consoleErrors).toEqual([]);
});
