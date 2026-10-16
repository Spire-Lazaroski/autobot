// Service worker of the phone app (installable web app). It keeps the app's own files (pages, scripts, styles,
// icons) so it opens fast, and shows a short "no connection" page when offline. It never stores account data:
// /api, /ws, /reports, /docs and the sign-in always go to the server.
const CACHE = "spire-shell-v1";
const NEVER = ["/api/", "/ws/", "/reports/", "/docs/", "/login", "/health"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(["/manifest.webmanifest", "/icons/icon-192.png"])).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

const OFFLINE = `<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spire Analytics</title><body style="margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#1a1a2e;color:#eceef5;font:15px Inter,system-ui,sans-serif;text-align:center;padding:24px">
<div><div style="font:800 22px Manrope,sans-serif;letter-spacing:.3em">SPIRE</div>
<p style="color:#9aa0b4">No connection to the lab. The bot keeps running on the server; this page reloads when you're back online.</p>
<script>addEventListener("online",()=>location.reload())</script></div>`;

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin || NEVER.some((p) => url.pathname.startsWith(p))) return;
  if (req.mode === "navigate") {                       // pages: always fresh from the server; offline page if unreachable
    e.respondWith(fetch(req).catch(() => new Response(OFFLINE, { headers: { "Content-Type": "text/html; charset=utf-8" } })));
    return;
  }
  if (url.pathname.startsWith("/assets/") || url.pathname.startsWith("/icons/")) {   // built files: names change with every build
    e.respondWith(caches.open(CACHE).then(async (c) => {
      const hit = await c.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok) c.put(req, res.clone());
      return res;
    }));
  }
});
