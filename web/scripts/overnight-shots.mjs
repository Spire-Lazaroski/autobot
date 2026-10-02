// Screenshots of the Lab's overnight-study panel (read-only: it only shows the saved study).
// Usage: node web/scripts/overnight-shots.mjs <out dir>   (LAB_URL or WEB_PORT pick the lab)
import { chromium } from "playwright-core";
import { existsSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

const OUT = resolve(process.argv[2] ?? "docs/img");
const BASE = process.env.LAB_URL ?? `http://127.0.0.1:${process.env.WEB_PORT ?? 5190}`;
const EDGE = ["C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ executablePath: EDGE, headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
await page.goto(`${BASE}/#/lab`);
// the sticky header would otherwise be painted into the middle of a tall element screenshot
await page.addStyleTag({ content: ".sticky.top-0 { position: static !important; }" });
const panel = page.locator("section", { hasText: "Overnight: sell before the close or hold?" }).first();
await panel.waitFor({ timeout: 60000 });
await page.waitForSelector("text=Out of sample: the walk-forward", { timeout: 60000 });
await panel.scrollIntoViewIfNeeded();
await page.waitForTimeout(2500);
await panel.screenshot({ path: `${OUT}/overnight_long.png` });
console.log("wrote overnight_long");
await panel.getByRole("button", { name: /^last 90 days$/ }).click();
await panel.locator("select[aria-label=Symbol]").selectOption("SPY");
await page.mouse.move(0, 0);
await page.waitForTimeout(2000);
await panel.screenshot({ path: `${OUT}/overnight_90d_spy.png` });
console.log("wrote overnight_90d_spy");
await browser.close();
