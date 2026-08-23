import { chromium } from "playwright";

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const page = await browser.newPage({ viewport: { width: 1600, height: 960 } });

const requests = [];
page.on("response", (res) => {
  if (res.url().includes("localhost:8000")) {
    requests.push(`${res.status()} ${res.request().method()} ${res.url()} [${res.headers()["content-type"]}]`);
  }
});

const consoleErrors = [];
page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});

await page.goto("http://localhost:3458/live", { waitUntil: "networkidle" });

// Give the WebSocket a few seconds to connect and the backend a few
// seconds to emit at least one real zone-crossing alert.
await page.waitForTimeout(15000);

await page.screenshot({ path: "screenshots/live_integrated.png" });

const badgeText = await page.locator("text=● backend").count();
const liveTag = await page.locator("text=LIVE").count();
const alertsText = await page.locator("text=Live Alerts").locator("..").innerText();

console.log("--- Backend HTTP requests seen by the frontend ---");
console.log(requests.join("\n") || "(none)");
console.log("\n--- '● backend' connected badge present:", badgeText > 0);
console.log("--- 'LIVE' video tag present (vs OFFLINE placeholder):", liveTag > 0);
console.log("\n--- Live Alerts panel text ---");
console.log(alertsText);
console.log("\n--- Console errors ---");
console.log(consoleErrors.join("\n") || "(none)");

await browser.close();
