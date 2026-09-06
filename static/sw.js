/* Shell-only cache, so the app installs and reopens instantly. API responses
   are never cached: job status, progress and the engraved SVG/PDF/MIDI are
   all served from here, and a stale cached copy of any of them would be a
   silent lie about whether a transcription actually finished.

   Every fetch here passes {cache: 'reload'}, which forces the browser to hit
   the network and skip its own HTTP disk cache for the freshness decision --
   still storing the response afterward, just never trusting a cached copy to
   answer "is this current?". A plain fetch() does not do that: it can
   silently resolve from disk cache with no network round trip if the
   browser's own heuristic freshness window hasn't lapsed, which would starve
   both the install step (a stale shell baked in on first cache) and the
   runtime network-first path (a code fix that never reaches an open tab) --
   this exact bug already bit a sibling project's service worker once
   (cycliq, 2026-08-06). Bump CACHE on any shell change so activate's
   cleanup still runs. */

const CACHE = 'clefline-shell-v1';
const SHELL = [
  '/',
  '/style.css',
  '/app.js',
  '/icon.svg',
  '/manifest.json',
];

async function cacheShellFresh() {
  const cache = await caches.open(CACHE);
  await Promise.all(
    SHELL.map(async (url) => {
      const response = await fetch(url, { cache: 'reload' });
      await cache.put(url, response);
    }),
  );
}

self.addEventListener('install', (event) => {
  event.waitUntil(cacheShellFresh().then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.pathname.startsWith('/api/')) return;

  event.respondWith(
    fetch(event.request, { cache: 'reload' })
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((c) => c.put(event.request, copy)).catch(() => {});
        return response;
      })
      .catch(() => caches.match(event.request).then((hit) => hit || caches.match('/'))),
  );
});
