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

await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.fill("#operatorId", "OP-774");
await page.fill("#passcode", "sentinel2026");
await page.click('button[type="submit"]');
await page.waitForTimeout(1500);

console.log("1) landed on:", page.url());
await page.waitForTimeout(3000); // let CAM-01 (default active) connect
await page.screenshot({ path: "screenshots/mc_1_cam01.png" });
const cam01Live = await page.locator("text=LIVE").count();
console.log("   CAM-01 shows LIVE badge:", cam01Live > 0);

// Switch to CAM-02 via the thumbnail row.
await page.getByRole("button", { name: /CAM-02/i }).click();
await page.waitForTimeout(3000);
await page.screenshot({ path: "screenshots/mc_2_cam02.png" });
const cam02Live = await page.locator("text=LIVE").count();
const cam02Label = await page.locator("text=/CAM-02/").count();
console.log("2) after switching to CAM-02 -> LIVE badge:", cam02Live > 0, " label shows CAM-02:", cam02Label > 0);

// Switch to CAM-03.
await page.getByRole("button", { name: /CAM-03/i }).click();
await page.waitForTimeout(3000);
await page.screenshot({ path: "screenshots/mc_3_cam03.png" });
const cam03Live = await page.locator("text=LIVE").count();
console.log("3) after switching to CAM-03 -> LIVE badge:", cam03Live > 0);

// Switch to CAM-04 (unconfigured) -> should show OFFLINE placeholder, not hang on stale CAM-03 video.
await page.getByRole("button", { name: /CAM-04/i }).click();
await page.waitForTimeout(2000);
await page.screenshot({ path: "screenshots/mc_4_cam04_offline.png" });
const cam04Offline = await page.locator("text=/OFFLINE/i").count();
console.log("4) CAM-04 (unconfigured) shows OFFLINE placeholder:", cam04Offline > 0);

// Back to CAM-01 to confirm it reconnects cleanly (not stuck from CAM-04's failure).
await page.getByRole("button", { name: /CAM-01/i }).click();
await page.waitForTimeout(3000);
await page.screenshot({ path: "screenshots/mc_5_back_to_cam01.png" });
const cam01LiveAgain = await page.locator("text=LIVE").count();
console.log("5) back on CAM-01 after visiting an offline camera -> LIVE again:", cam01LiveAgain > 0);

// Thermal toggle is per-camera: turn it on for CAM-01, then switch to CAM-02 and confirm
// CAM-02's toggle independently reads OFF (not leaking CAM-01's state).
const thermalBtn = page.getByRole("button", { name: /simulated thermal view/i });
await thermalBtn.click();
await page.waitForTimeout(1500);
const cam01ThermalOn = await page.locator("text=THERMAL ON").count();
console.log("6) CAM-01 thermal toggled ON:", cam01ThermalOn > 0);

await page.getByRole("button", { name: /CAM-02/i }).click();
await page.waitForTimeout(2500);
const cam02ThermalState = await page.locator("text=THERMAL OFF").count();
console.log("7) CAM-02 thermal independently OFF (not leaked from CAM-01):", cam02ThermalState > 0);
await page.screenshot({ path: "screenshots/mc_6_cam02_thermal_independent.png" });

console.log("\n--- Console errors ---");
console.log(consoleErrors.join("\n") || "(none)");

await browser.close();
