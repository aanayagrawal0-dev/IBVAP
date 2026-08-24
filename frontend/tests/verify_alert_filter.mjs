import { chromium } from 'playwright';

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

await page.goto('http://localhost:3000/login');
await page.fill('input[name="operatorId"], input#operatorId, input[type="text"]', 'OP-774');
await page.fill('input[type="password"]', 'sentinel2026');
await page.click('button[type="submit"]');
await page.waitForURL('**/live', { timeout: 10000 });
await page.waitForTimeout(2500);

await page.screenshot({ path: '/tmp/live_default.png' });

// Add a zone via zone-config first would take time; instead just verify filter UI directly.
// Click filter chip CAM-03
const camBtns = await page.locator('button:has-text("CAM-03")').all();
console.log('CAM-03 buttons found:', camBtns.length);

// Find the filter chip specifically (small pill button, not thumbnail)
const filterGroup = page.locator('[role="group"][aria-label="Filter alerts by camera"]');
await filterGroup.waitFor({ timeout: 5000 });
await page.screenshot({ path: '/tmp/live_filter_default.png' });

await filterGroup.locator('button:has-text("CAM-03")').click();
await page.waitForTimeout(500);
await page.screenshot({ path: '/tmp/live_filter_cam03.png' });

await filterGroup.locator('button:has-text("CAM-01")').click();
await page.waitForTimeout(500);
await page.screenshot({ path: '/tmp/live_filter_cam03_cam01.png' });

await filterGroup.locator('button:has-text("All")').click();
await page.waitForTimeout(500);
await page.screenshot({ path: '/tmp/live_filter_all.png' });

await browser.close();
console.log('done');
