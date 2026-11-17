const SHELL_CACHE = "mymusic-shell-v1";
const OFFLINE_AUDIO_CACHE = "mymusic-offline-audio-v1";
const SHELL_ASSETS = ["/", "/static/style.css", "/static/app.js", "/static/logo.svg", "/static/manifest.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_ASSETS).catch(() => {}))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Serve offline-saved audio tracks from OFFLINE_AUDIO_CACHE when available
  if (url.pathname.startsWith("/stream/") || url.pathname.startsWith("/covers/")) {
    event.respondWith(
      caches.open(OFFLINE_AUDIO_CACHE).then(async (cache) => {
        const cached = await cache.match(event.request);
        if (cached) return cached;
        return fetch(event.request);
      })
    );
    return;
  }

  // Never intercept API/auth mutations
  if (event.request.method !== "GET" || url.pathname.startsWith("/api/")) {
    return;
  }

  // Network-first for HTML & static assets, fallback to shell cache
  event.respondWith(
    fetch(event.request)
      .then((resp) => {
        if (resp.ok && SHELL_ASSETS.includes(url.pathname)) {
          const clone = resp.clone();
          caches.open(SHELL_CACHE).then((c) => c.put(event.request, clone));
        }
        return resp;
      })
      .catch(() => caches.match(event.request))
  );
});
