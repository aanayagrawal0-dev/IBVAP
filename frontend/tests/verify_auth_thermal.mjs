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

// 1. Unauthenticated visit to a protected route should redirect to /login.
await page.goto(`${BASE}/live`, { waitUntil: "networkidle" });
await page.waitForTimeout(500);
console.log("1) unauth /live -> url:", page.url());
await page.screenshot({ path: "screenshots/1_redirected_to_login.png" });

// 2. Wrong credentials -> error shown, still on /login.
await page.fill("#operatorId", "OP-774");
await page.fill("#passcode", "wrong-passcode");
await page.click('button[type="submit"]');
await page.waitForTimeout(600);
const errorVisible = await page.locator("#login-error").count();
console.log("2) wrong creds -> still on /login:", page.url().includes("/login"), " error shown:", errorVisible > 0);
await page.screenshot({ path: "screenshots/2_login_error.png" });

// 3. Correct credentials -> redirected to /live, sidebar shows operator id.
await page.fill("#passcode", "sentinel2026");
await page.click('button[type="submit"]');
await page.waitForTimeout(1200);
console.log("3) correct creds -> url:", page.url());
const sidebarOperator = await page.locator("text=OP-774").count();
console.log("   sidebar shows OP-774:", sidebarOperator > 0);
await page.screenshot({ path: "screenshots/3_live_after_login.png" });

// Give the MJPEG stream + WS a moment to connect for a convincing shot.
await page.waitForTimeout(4000);
await page.screenshot({ path: "screenshots/4_live_connected.png" });

// 4. Thermal toggle: click it, confirm label flips to ON, screenshot the
// visibly false-colored feed.
const thermalButton = page.getByRole("button", { name: /simulated thermal view/i });
await thermalButton.click();
await page.waitForTimeout(2500);
const thermalLabel = await page.locator("text=THERMAL ON").count();
console.log("4) thermal toggled ON, button now reads THERMAL ON:", thermalLabel > 0);
await page.screenshot({ path: "screenshots/5_thermal_on.png" });

// 5. Reload the page — session should persist (localStorage) and thermal
// state should be reflected from the backend (GET /api/thermal) again.
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(2500);
console.log("5) after reload -> url (should stay /live):", page.url());
const thermalStillOn = await page.locator("text=THERMAL ON").count();
console.log("   thermal still ON after reload:", thermalStillOn > 0);
await page.screenshot({ path: "screenshots/6_after_reload.png" });

// turn thermal back off, tidy state for future runs
const thermalButton2 = page.getByRole("button", { name: /simulated thermal view/i });
await thermalButton2.click();
await page.waitForTimeout(1000);

// 6. Logout -> back to /login, and protected route now redirects again.
await page.click('button[aria-label="Log out"]');
await page.waitForTimeout(800);
console.log("6) after logout -> url:", page.url());
await page.goto(`${BASE}/live`, { waitUntil: "networkidle" });
await page.waitForTimeout(500);
console.log("   revisiting /live after logout -> url:", page.url());
await page.screenshot({ path: "screenshots/7_after_logout.png" });

console.log("\n--- Console errors ---");
console.log(consoleErrors.join("\n") || "(none)");

await browser.close();
