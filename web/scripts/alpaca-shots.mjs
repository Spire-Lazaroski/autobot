// Screenshots of the customer-facing pages for the Alpaca Connect app registration (1024 x 500, PNG).
// Usage (in web/, after `npm run build`):  node scripts/alpaca-shots.mjs ../docs/img/alpaca
// Serves web/dist itself and answers /api with an example invited user ("ana"): no lab, no real account.
import { chromium } from "playwright-core";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { extname, resolve } from "node:path";

const OUT = resolve(process.argv[2] ?? "../docs/img/alpaca");
const DIST = resolve("dist");
const EDGE = ["C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
const BASE = "https://spire.example";
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".png": "image/png", ".svg": "image/svg+xml",
  ".webmanifest": "application/manifest+json", ".json": "application/json", ".woff2": "font/woff2" };
mkdirSync(OUT, { recursive: true });

let connected = false;
const api = (path) => {
  if (path === "/api/me") return { user: "ana", role: "user", login_required: true };
  if (path === "/api/health") return { ok: true, alpaca_keys: true, strategies: 18, timeframes: [] };
  if (path === "/api/alpaca/status") return connected
    ? { connected: true, method: "oauth", account_tail: "4821", since: Date.now() / 1000, oauth_available: true }
    : { connected: false, oauth_available: true };
  return null;                          // like the real server: an invited user gets nothing else (403)
};

const browser = await chromium.launch({ executablePath: EDGE, headless: true });
const page = await browser.newPage({ viewport: { width: 1024, height: 500 }, deviceScaleFactor: 1 });
await page.route(`${BASE}/**`, async (route) => {
  const url = new URL(route.request().url());
  if (url.pathname.startsWith("/api/")) {
    const body = api(url.pathname);
    return body ? route.fulfill({ json: body }) : route.fulfill({ status: 403, json: { detail: "Your own account is not connected yet." } });
  }
  let p = url.pathname === "/" ? "/index.html" : url.pathname;
  if (p === "/login") p = "/login.html";
  if (p === "/terms") p = "/terms.html";
  if (p === "/privacy") p = "/privacy.html";
  const file = resolve(DIST + p);
  if (!file.startsWith(DIST) || !existsSync(file)) return route.fulfill({ status: 404, body: "" });
  return route.fulfill({ body: readFileSync(file), contentType: TYPES[extname(file)] ?? "application/octet-stream" });
});
const shot = async (name) => { await page.screenshot({ path: `${OUT}/${name}.png` }); console.log("wrote", name); };

await page.goto(`${BASE}/login`);
await page.waitForTimeout(4500);                              // the mark assembles, the wordmark appears
await shot("1_sign_in");

await page.goto(`${BASE}/login?invite=example`);
await page.waitForTimeout(4500);
await shot("2_create_account");

await page.goto(`${BASE}/#/`);
await page.waitForSelector("#alpaca-disclosure");
await page.addStyleTag({ content: ".sticky{position:static!important}" });
await page.locator("#alpaca-disclosure").evaluate((el) => el.scrollIntoView({ block: "center" }));
await page.waitForTimeout(600);
await shot("3_connect_with_disclosure");

connected = true;
await page.goto(`${BASE}/#/?alpaca=connected`);
await page.reload();                                          // a fresh load, as after Alpaca's return trip
await page.waitForSelector("text=Connected: paper account");
await page.waitForTimeout(600);
await shot("4_connected");

await page.goto(`${BASE}/terms`);
await page.waitForTimeout(800);
await shot("5_terms_of_use");

await page.goto(`${BASE}/privacy`);
await page.waitForTimeout(800);
await shot("6_privacy_policy");

await browser.close();
