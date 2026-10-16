// The phone app's icons (web/public/icons), drawn from the Spire mark: a navy triangle with the gold "A".
// Usage (in web/):  node scripts/gen-icons.mjs        (uses the installed Edge, headless; nothing is downloaded)
import { chromium } from "playwright-core";
import { existsSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

const OUT = resolve("public/icons");
const EDGE = ["C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
mkdirSync(OUT, { recursive: true });

// pad: share of the square around the mark (maskable icons need a safe zone: the OS may cut a circle out of it)
// The chosen design (variation 6): the triangle with a large gold "A", and SPIRE / ANALYTICS under it.
const svg = (pad, bg) => `
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#26263f"/><stop offset="1" stop-color="#0e0e1c"/></linearGradient>
    <linearGradient id="tri" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3a3a58"/><stop offset="1" stop-color="#1a1a2e"/></linearGradient>
    <linearGradient id="gold" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f2c45e"/><stop offset="1" stop-color="#d98c2b"/></linearGradient>
  </defs>
  ${bg ? '<rect width="100" height="100" fill="url(#bg)"/>' : ""}
  <g transform="translate(${pad} ${pad}) scale(${(100 - 2 * pad) / 100})">
    <g transform="translate(22 4) scale(.56)">
      <polygon points="50,6 4,94 96,94" fill="url(#tri)" stroke="#e8b23d" stroke-opacity="0.45" stroke-width="1.8" stroke-linejoin="round"/>
      <g transform="translate(50 92) scale(1.32) translate(-50 -86)">
        <path d="M50 38 L33 86 H41 L45 74 H55 L59 86 H67 Z M50 52 L47 66 H53 Z" fill="url(#gold)" fill-rule="evenodd"/>
      </g>
    </g>
    <text x="50" y="78" text-anchor="middle" font-family="Manrope,'Segoe UI',sans-serif" font-weight="800" font-size="12" letter-spacing="3.4" fill="#eceef5">SPIRE</text>
    <text x="50" y="89" text-anchor="middle" font-family="Inter,'Segoe UI',sans-serif" font-weight="600" font-size="4.8" letter-spacing="2.6" fill="#e8b23d">ANALYTICS</text>
  </g>
</svg>`;

const ICONS = [
  { name: "icon-192.png", size: 192, pad: 0, bg: true },
  { name: "icon-512.png", size: 512, pad: 0, bg: true },
  { name: "maskable-512.png", size: 512, pad: 12, bg: true },   // the OS may crop to a circle: keep it inside
  { name: "apple-touch-icon.png", size: 180, pad: 0, bg: true },
];

const browser = await chromium.launch({ executablePath: EDGE, headless: true });
for (const ic of ICONS) {
  const page = await browser.newPage({ viewport: { width: ic.size, height: ic.size }, deviceScaleFactor: 1 });
  await page.setContent(`<html><head><link href="https://fonts.googleapis.com/css2?family=Inter:wght@600&family=Manrope:wght@800&display=swap" rel="stylesheet"></head><body style="margin:0;background:transparent">${svg(ic.pad, ic.bg).replace("<svg ", `<svg width="${ic.size}" height="${ic.size}" `)}</body></html>`);
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `${OUT}/${ic.name}`, omitBackground: true, clip: { x: 0, y: 0, width: ic.size, height: ic.size } });
  await page.close();
  console.log("wrote", ic.name);
}
await browser.close();
