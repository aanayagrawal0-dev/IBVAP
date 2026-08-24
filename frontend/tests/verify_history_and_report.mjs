import { chromium } from "playwright";

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

await page.goto("http://localhost:3000/login");
await page.fill('input[type="text"]', "OP-774");
await page.fill('input[type="password"]', "sentinel2026");
await page.click('button[type="submit"]');
await page.waitForURL("**/live", { timeout: 10000 });

// --- History page ---
await page.goto("http://localhost:3000/history");
await page.waitForTimeout(1500);
await page.screenshot({ path: "/tmp/history_default.png" });

// Severity filter
await page.selectOption('select:near(:text("Severity"))', "critical").catch(async () => {
  // fallback: select by label text search
  const selects = await page.locator("select").all();
  console.log("selects found:", selects.length);
});
await page.waitForTimeout(800);
await page.screenshot({ path: "/tmp/history_critical_filter.png" });

// Reset severity, test pagination
await page.selectOption('select >> nth=1', "all");
await page.waitForTimeout(800);
const nextBtn = page.locator('button:has-text("Next")');
await nextBtn.click();
await page.waitForTimeout(800);
await page.screenshot({ path: "/tmp/history_page2.png" });

// --- Analytics page: Generate Report ---
await page.goto("http://localhost:3000/analytics");
await page.waitForTimeout(1000);

const [download] = await Promise.all([
  page.waitForEvent("download", { timeout: 15000 }),
  page.click('button:has-text("Generate Report")'),
]);
const downloadPath = "/tmp/downloaded_report.pdf";
await download.saveAs(downloadPath);
console.log("downloaded:", download.suggestedFilename());

await page.screenshot({ path: "/tmp/analytics_after_report.png" });

await browser.close();
console.log("done");
