import { chromium } from "playwright";
import fs from "fs";

fs.mkdirSync("screenshots", { recursive: true });

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const page = await browser.newPage({ viewport: { width: 1600, height: 960 } });

const consoleErrors = [];
page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});

const BASE = "http://localhost:3000";

// Log in first — the whole dashboard is gated now.
await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.fill("#operatorId", "OP-774");
await page.fill("#passcode", "sentinel2026");
await page.click('button[type="submit"]');
await page.waitForTimeout(1000);

await page.goto(`${BASE}/zone-config`, { waitUntil: "networkidle" });
await page.waitForTimeout(1000);
console.log("1) loaded zone-config, url:", page.url());
await page.screenshot({ path: "screenshots/zc_1_initial_cam01.png" });

// The default persisted zone for CAM-01 should already be listed.
const initialZoneCount = await page.locator("aside, div").locator("text=/pt$/").count();
console.log("   zones listed for CAM-01 (has 'Npt' label):", initialZoneCount);

// Add a second zone to CAM-01 by drawing a small triangle.
await page.getByRole("button", { name: "New Zone" }).click();
// "New Zone" already switches into add-point mode automatically.
const canvas = page.locator("div.aspect-video").first();
const box = await canvas.boundingBox();
const pts = [
  [box.x + box.width * 0.1, box.y + box.height * 0.1],
  [box.x + box.width * 0.25, box.y + box.height * 0.1],
  [box.x + box.width * 0.18, box.y + box.height * 0.3],
];
for (const [x, y] of pts) {
  await page.mouse.click(x, y);
  await page.waitForTimeout(150);
}
await page.screenshot({ path: "screenshots/zc_2_drew_second_zone_cam01.png" });

const zoneCountAfterDraw = await page.locator("li").count();
console.log("2) zone list items after adding a 2nd zone:", zoneCountAfterDraw);

// Save.
await page.getByRole("button", { name: /save all/i }).click();
await page.waitForTimeout(1500);
const savedMsg = await page.locator('[role="status"]').innerText().catch(() => "");
console.log("3) save status message:", savedMsg);
await page.screenshot({ path: "screenshots/zc_3_saved_cam01.png" });

// Reload the page entirely — zones should round-trip through the real backend.
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(1000);
const zoneItemsAfterReload = await page.locator("li").count();
console.log("4) zone list items after full page reload (persistence check):", zoneItemsAfterReload);
await page.screenshot({ path: "screenshots/zc_4_after_reload_cam01.png" });

// Switch to CAM-02, draw a zone there too, confirm it's isolated from CAM-01.
await page.selectOption("#camera-select", "CAM-02");
await page.waitForTimeout(800);
const cam02ZonesBeforeAdd = await page.locator("li").count();
console.log("5) CAM-02 zones on first visit (should be 0, isolated from CAM-01):", cam02ZonesBeforeAdd);

await page.getByRole("button", { name: "New Zone" }).click();
const pts2 = [
  [box.x + box.width * 0.4, box.y + box.height * 0.4],
  [box.x + box.width * 0.6, box.y + box.height * 0.4],
  [box.x + box.width * 0.6, box.y + box.height * 0.6],
  [box.x + box.width * 0.4, box.y + box.height * 0.6],
];
for (const [x, y] of pts2) {
  await page.mouse.click(x, y);
  await page.waitForTimeout(150);
}
await page.getByRole("button", { name: /save all/i }).click();
await page.waitForTimeout(1500);
const cam02SavedMsg = await page.locator('[role="status"]').innerText().catch(() => "");
console.log("6) CAM-02 save status (should say NOT hot-reloaded, not the live camera):", cam02SavedMsg);
await page.screenshot({ path: "screenshots/zc_5_cam02_saved.png" });

// Switch back to CAM-01 -- its 2 zones should still be there, untouched by CAM-02 edits.
await page.selectOption("#camera-select", "CAM-01");
await page.waitForTimeout(800);
const cam01ZonesAgain = await page.locator("li").count();
console.log("7) back on CAM-01, zone count still intact:", cam01ZonesAgain);
await page.screenshot({ path: "screenshots/zc_6_back_to_cam01.png" });

console.log("\n--- Console errors ---");
console.log(consoleErrors.join("\n") || "(none)");

await browser.close();
