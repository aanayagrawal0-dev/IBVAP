import { chromium } from "playwright";

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

await page.goto("http://localhost:3000/login");
await page.fill('input[type="text"]', "OP-774");
await page.fill('input[type="password"]', "sentinel2026");
await page.click('button[type="submit"]');
await page.waitForURL("**/live");

await page.goto("http://localhost:3000/history");
await page.waitForTimeout(1500);
await page.screenshot({ path: "/tmp/history_collapsed.png" });

// Click the first row to expand -> should show the "no API key" error state
const firstRow = page.locator('tr[role="button"]').first();
await firstRow.click();
await page.waitForTimeout(1200);
await page.screenshot({ path: "/tmp/history_expanded_error.png" });

// Click again to collapse
await firstRow.click();
await page.waitForTimeout(500);
await page.screenshot({ path: "/tmp/history_collapsed_again.png" });

// Keyboard accessibility: focus + Enter
await firstRow.focus();
await page.keyboard.press("Enter");
await page.waitForTimeout(800);
await page.screenshot({ path: "/tmp/history_expanded_keyboard.png" });

await browser.close();
console.log("done");
