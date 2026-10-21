const CACHE_NAME = 'barcode-hub-shell-v3';
const PUBLIC_PAGES = new Set(['/', '/offline', '/pricing', '/docs', '/catalogue', '/privacy']);
const SHELL = [
  '/', '/offline', '/pricing', '/docs', '/catalogue', '/privacy',
  '/static/css/style.css', '/static/js/app.js', '/manifest.webmanifest',
  '/static/icons/icon-192.png', '/static/icons/icon-512.png', '/static/icons/favicon.svg'
];
const NEVER_CACHE = [
  '/account', '/login', '/register', '/checkout', '/promise212324',
  '/resources/', '/api/', '/downloads/', '/logout', '/subscribe'
];
const isNeverCache = (path) => NEVER_CACHE.some((prefix) => path.startsWith(prefix));

self.addEventListener('install', (event) => {
  // Cache each shell resource independently: one failed request must not cancel
  // all other good cache entries and must never prevent the SW from installing.
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    await Promise.all(SHELL.map(async (url) => {
      try {
        const response = await fetch(new Request(url, {cache: 'reload', credentials: 'same-origin'}));
        if (response.ok && response.type === 'basic' && !isNeverCache(new URL(url, self.location.origin).pathname)) {
          await cache.put(url, response.clone());
        }
      } catch (_) { /* Best effort; runtime requests retry when online. */ }
    }));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((key) => key.startsWith('barcode-hub-shell-') && key !== CACHE_NAME).map((key) => caches.delete(key)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || isNeverCache(url.pathname)) return;

  if (req.mode === 'navigate') {
    // Query results are user-specific and never cached. Unknown pages are not
    // cached either; offline mode falls back to the compact static offline page.
    const cacheablePath = !url.search && PUBLIC_PAGES.has(url.pathname);
    event.respondWith((async () => {
      const cache = await caches.open(CACHE_NAME);
      try {
        const response = await fetch(req);
        if (cacheablePath && response.ok && response.headers.get('X-Offline-Cacheable') === 'public'
            && !isNeverCache(url.pathname) && !response.headers.get('Cache-Control')?.includes('no-store')) {
          await cache.put(url.pathname, response.clone()).catch(() => {});
        }
        return response;
      } catch (_) {
        if (cacheablePath) {
          const cached = await cache.match(url.pathname);
          if (cached) return cached;
        }
        return (await cache.match('/offline')) || new Response(
          '<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><title>Offline</title><main><h1>You are offline</h1><p>Reconnect for live searches, accounts, payments and API lookups.</p></main>',
          {status: 503, headers: {'Content-Type': 'text/html; charset=utf-8'}}
        );
      }
    })());
    return;
  }

  // Static shell assets use cache-first for quick repeat loads, then refresh in
  // the background. Only same-origin responses are stored.
  if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest') {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE_NAME);
      const cached = await cache.match(req);
      if (cached) {
        fetch(req).then((response) => { if (response.ok && response.type === 'basic') cache.put(req, response.clone()).catch(() => {}); }).catch(() => {});
        return cached;
      }
      try {
        const response = await fetch(req);
        if (response.ok && response.type === 'basic') await cache.put(req, response.clone()).catch(() => {});
        return response;
      } catch (_) {
        return new Response('', {status: 503});
      }
    })());
  }
});
