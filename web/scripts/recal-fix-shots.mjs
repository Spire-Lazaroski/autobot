// Screenshots of the Recalibrate page's switch study section (read-only: it presses no buttons
// except the period / version selectors of the section itself).
// Usage: node web/scripts/recal-fix-shots.mjs <out dir>   (LAB_URL or WEB_PORT pick the lab)
import { chromium } from "playwright-core";
import { existsSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

const OUT = resolve(process.argv[2] ?? "docs/img");
const BASE = process.env.LAB_URL ?? `http://127.0.0.1:${process.env.WEB_PORT ?? 5190}`;
const EDGE = ["C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ executablePath: EDGE, headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
await page.goto(`${BASE}/#/recal`);
await page.addStyleTag({ content: ".sticky.top-0 { position: static !important; }" });
const panel = page.locator("section", { hasText: "Switch study" }).first();
await panel.waitFor({ timeout: 60000 });
await page.waitForSelector("text=Per symbol:", { timeout: 60000 });
await panel.scrollIntoViewIfNeeded();
await page.waitForTimeout(2500);
await panel.screenshot({ path: `${OUT}/recal_fix_confirm.png` });
console.log("wrote recal_fix_confirm");
await panel.getByRole("button", { name: "Design period" }).click();
await page.mouse.move(0, 0);
await page.waitForTimeout(1500);
await panel.screenshot({ path: `${OUT}/recal_fix_design.png` });
console.log("wrote recal_fix_design");
await browser.close();
