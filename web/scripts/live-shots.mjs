// Screenshots of the Live page's charts and the folded / unfolded jobs editor.
// Read-only: it never presses Start, Stop or Save; it only unfolds the editor in its own browser.
// Usage: node web/scripts/live-shots.mjs <out dir>   (LAB_URL or WEB_PORT pick the lab)
import { chromium } from "playwright-core";
import { existsSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

const OUT = resolve(process.argv[2] ?? "docs/img");
const BASE = process.env.LAB_URL ?? `http://127.0.0.1:${process.env.WEB_PORT ?? 5190}`;
const EDGE = ["C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ executablePath: EDGE, headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
// SIMULATE_HELD=1: two pretend positions are added to the live feed in this browser only (nothing is
// sent anywhere), to show the per-position charts when the account holds nothing.
if (process.env.SIMULATE_HELD) {
  await page.routeWebSocket(/\/ws\/live/, (ws) => {
    const server = ws.connectToServer();
    server.onMessage((m) => {
      const d = JSON.parse(m);
      const last = (s) => d.status?.symbols?.[s]?.price ?? 100;
      d.positions = ["SPY", "TSLA"].map((s, i) => ({ symbol: s, side: "long", qty: i ? 27 : 13, avg_entry_price: last(s) * (i ? 1.004 : 0.997),
        current_price: last(s), market_value: last(s) * (i ? 27 : 13), unrealized_pl: last(s) * (i ? -0.108 : 0.039),
        unrealized_plpc: i ? -0.4 : 0.3, unrealized_intraday_pl: 0 }));
      ws.send(JSON.stringify(d));
    });
  });
}
await page.goto(`${BASE}/#/live`);
await page.addStyleTag({ content: ".sticky.top-0 { position: static !important; }" });
await page.waitForSelector("text=Paper equity", { timeout: 60000 });
await page.waitForTimeout(6000);
const tag = process.env.SIMULATE_HELD ? "_simulated" : "";
await page.screenshot({ path: `${OUT}/live_charts${tag}.png`, fullPage: true });
console.log(`wrote live_charts${tag}`);
const jobs = page.locator("section", { hasText: "Jobs · config.yaml" }).first();
await jobs.scrollIntoViewIfNeeded();
await jobs.screenshot({ path: `${OUT}/live_jobs_folded.png` });
console.log("wrote live_jobs_folded");
await jobs.getByRole("button", { name: /Jobs · config.yaml/ }).click();
await page.waitForTimeout(800);
await jobs.screenshot({ path: `${OUT}/live_jobs_open.png` });
console.log("wrote live_jobs_open");
await browser.close();
