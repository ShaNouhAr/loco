/* Service worker loco. Cache la coque statique (versionnée par l'empreinte des assets) et sert une page hors-ligne
   pour les navigations quand le réseau est absent. Les requêtes dynamiques (pages, API, agent) passent par le réseau. */
const CACHE = "loco-{{ v }}";
const SHELL = [
  "/static/style.css?v={{ v }}",
  "/static/app.js?v={{ v }}",
  "/static/theme.js?v={{ v }}",
  "/static/logo.svg?v={{ v }}",
  "/static/icons/icon-192.png",
  "/offline"
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.map((key) => (key === CACHE ? null : caches.delete(key)))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/static/")) {
    // Assets versionnés : cache d'abord, puis réseau (et on met en cache au passage).
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((resp) => {
        if (resp && resp.ok) { const copy = resp.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
        return resp;
      }))
    );
    return;
  }

  if (req.mode === "navigate") {
    // Pages : réseau d'abord, repli sur la page hors-ligne si injoignable.
    event.respondWith(fetch(req).catch(() => caches.match("/offline")));
    return;
  }
  // Tout le reste (API, agent, téléchargements) : réseau uniquement.
});
