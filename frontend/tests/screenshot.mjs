import { chromium } from "playwright";
import path from "node:path";

const pages = ["/live", "/zone-config", "/history", "/analytics"];
const outDir = path.join(process.cwd(), "screenshots");

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const page = await browser.newPage({ viewport: { width: 1600, height: 960 } });

const consoleErrors = [];
page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(`[${msg.type()}] ${msg.text()}`);
});
page.on("pageerror", (err) => consoleErrors.push(`[pageerror] ${err.message}`));

for (const route of pages) {
  await page.goto(`http://localhost:3457${route}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  const file = path.join(outDir, `${route.replace("/", "") || "root"}.png`);
  await page.screenshot({ path: file });
  console.log(`Saved ${file}`);
}

await browser.close();

if (consoleErrors.length) {
  console.log("\n--- CONSOLE ERRORS ---");
  console.log(consoleErrors.join("\n"));
} else {
  console.log("\nNo console errors across any page.");
}
