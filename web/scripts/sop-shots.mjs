// Screenshots for the SOP, taken from the running lab (start_lab.bat first).
// Usage: node web/scripts/sop-shots.mjs docs/img
// Read-only: it never presses Start, Save or a real Stop. The Stop dialog is captured with a
// simulated "running" bot and the stop request is intercepted, so no STOP file is ever written.
import { chromium } from "playwright-core";
import { existsSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

const OUT = resolve(process.argv[2] ?? "docs/img");
const BASE = process.env.LAB_URL ?? `http://127.0.0.1:${process.env.WEB_PORT ?? 5190}`;
const EDGE = ["C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ executablePath: EDGE, headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 860 }, deviceScaleFactor: 1 });
const shot = async (name, locator) => {
  await page.waitForTimeout(600);
  if (locator) await locator.screenshot({ path: `${OUT}/${name}.png` });
  else await page.screenshot({ path: `${OUT}/${name}.png` });
  console.log("wrote", name);
};

// 1. Live page: connection, bot state, account
await page.goto(`${BASE}/#/live`);
await page.waitForSelector("#status-band", { timeout: 60000 });
await page.waitForTimeout(3500);
await page.screenshot({ path: `${OUT}/live_top.png`, clip: { x: 0, y: 0, width: 1280, height: 330 } });
console.log("wrote live_top");
await shot("live_state", page.locator("#status-band"));
await shot("live_holdings", page.locator("#holdings-cards"));
// 2. Jobs editor
const jobs = page.locator("section", { hasText: "Jobs · config.yaml" }).first();
await jobs.scrollIntoViewIfNeeded();
await shot("jobs_editor", jobs);
// 3. Reports page
await page.goto(`${BASE}/#/reports`);
await page.waitForSelector("text=Automatic daily report");
await shot("reports", null);

// 4. News page: collector status, the event study and the news-guard study (top part of each panel)
const panelTop = async (name, title, maxH = 760) => {
  const p = page.locator("section.panel", { has: page.locator(".panel-title", { hasText: title }) }).first();
  await p.scrollIntoViewIfNeeded();
  await page.waitForTimeout(800);
  const b = await p.boundingBox();  // relative to the viewport; a full-page clip needs page coordinates
  const sy = await page.evaluate(() => window.scrollY);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true, clip: { x: b.x, y: b.y + sy, width: b.width, height: Math.min(b.height, maxH) } });
  console.log("wrote", name);
};
await page.goto(`${BASE}/#/news`);
await page.waitForSelector("text=Last received", { timeout: 60000 });
await page.waitForTimeout(2500);
await page.screenshot({ path: `${OUT}/news_top.png`, clip: { x: 0, y: 0, width: 1280, height: 300 } });
console.log("wrote news_top");
await page.addStyleTag({ content: ".sticky{position:static!important}" });  // the sticky header would cover full-page clips
await panelTop("news_impact", "Market impact of news", 700);
await panelTop("news_guard", "Does the news guard help?", 620);
// 5. Recalibrate page (read-only: no button is pressed)
await page.goto(`${BASE}/#/recal`);
await page.waitForSelector("text=Recalibration proposal", { timeout: 60000 });
await page.addStyleTag({ content: ".sticky{position:static!important}" });
await page.waitForTimeout(1500);
await panelTop("recal", "Recalibration proposal", 640);

// 6. Stop dialog with a simulated running bot (nothing is sent)
const ctx = await browser.newContext({ viewport: { width: 1280, height: 760 } });
const p2 = await ctx.newPage();
const snap = { time: 0, bot: { running: true, unresponsive: false, pid: 1234, heartbeat_age_s: 1, process_alive: true, stop_requested: false },
  status: { time: new Date().toISOString(), equity: 100000, "day_pnl_%": 0, market_open: true, halted: false, symbols: {},
    core: { enabled: true, symbol: "VOO", allocation_pct: 30, rebalance_band_pct: 5, qty: 55 },
    rotation: { enabled: true, allocation_pct: 40, top_n: 5, symbols: ["XLK", "XLV", "XLE"], held: { XLE: 59, XLK: 15, XLV: 36 } } },
  trades: [], log: [], ok: true, positions: [], orders: [],
  account: { number_tail: "DEMO", status: "ACTIVE", equity: 100000, last_equity: 100000, day_pnl: 0, day_pnl_pct: 0, cash: 100000, buying_power: 200000 } };
await p2.routeWebSocket(/\/ws\/live/, (ws) => { ws.send(JSON.stringify(snap)); });
await p2.route("**/api/live/stop", (r) => r.abort());
await p2.route("**/api/live/start", (r) => r.abort());
await p2.goto(`${BASE}/#/live`);
await p2.waitForSelector("text=bot running");
await p2.locator("button", { hasText: "Stop + flatten" }).first().click();
await p2.waitForSelector("role=dialog");
await p2.waitForTimeout(400);
await p2.screenshot({ path: `${OUT}/stop_dialog.png` });
console.log("wrote stop_dialog");
await p2.getByRole("button", { name: "Cancel" }).click();

await browser.close();
