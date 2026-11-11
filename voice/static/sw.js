const CACHE = 'sunday-v1';
const PRECACHE = ['/', '/static/sunday.png', '/static/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Network-first: always try live, fall back to cache for the shell only
self.addEventListener('fetch', e => {
  // WebSocket and API calls — never intercept
  if (e.request.url.includes('/ws') || e.request.url.includes('/presence')) return;

  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});
