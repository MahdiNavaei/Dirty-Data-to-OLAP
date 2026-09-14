import { expect, test } from "@playwright/test";
import path from "node:path";

const checkpoints = [
  "REVIEW_EVIDENCE_DECISIONS",
  "REVIEW_CANONICAL_IDENTITY",
  "REVIEW_ANALYTICAL_PLAN",
  "REVIEW_MATERIALIZATION_PLAN",
];

test("containerized Step29 real product path reaches eligible G6 output", async ({ page }) => {
  const consoleErrors: string[] = [];
  const directStorageRequests: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("request", (request) => {
    if (/\.sqlite|\.duckdb|workspace[\\/]/i.test(request.url())) directStorageRequests.push(request.url());
  });

  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
  await expect(page.locator("label[for=source-file]")).toBeVisible();
  await page.locator("#source-file").setInputFiles(path.resolve("e2e/fixtures/orders.csv"));
  await page.getByRole("button", { name: "Import and register" }).click();
  await expect(page.getByRole("status")).toContainText("Source imported");
  await page.getByRole("button", { name: "Create run and start discovery" }).click();
  await expect(page).toHaveURL(/\/runs\//);

  const accepted: string[] = [];
  for (const checkpoint of checkpoints) {
    await expect(page.locator(".checkpoint")).toContainText(checkpoint, { timeout: 150_000 });
    await page.getByRole("button", { name: /Accept and resume/ }).first().click();
    accepted.push(checkpoint);
  }

  await expect(page.getByTestId("run-status")).toHaveText("SUCCEEDED", { timeout: 180_000 });
  expect(accepted).toEqual(checkpoints);
  const body = await page.locator("body").innerText();
  expect(body).toMatch(/G6\s+PASS[^\n]*eligible/i);
  expect(body).toContain("Validated OLAP output available");
  expect(body).not.toMatch(/\bSELECT\b|\bCREATE\s+TABLE\b|\bINSERT\s+INTO\b|workspace[\\/]platform|workspace[\\/]runs|file_locator|O-100|C-1|10\.50/i);
  expect(directStorageRequests).toEqual([]);
  expect(consoleErrors).toEqual([]);
  expect(await page.locator("h1, h2, h3").count()).toBeGreaterThanOrEqual(8);
  await expect(page.locator("table caption")).toHaveCount(1);
});
