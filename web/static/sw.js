/* Service worker: офлайн-оболочка + сетевой приоритет для API.
 *
 * Стратегия:
 *  - статика (css/js/иконки) — cache-first: открывается мгновенно и работает
 *    без сети, поэтому приложение запускается даже в метро или в горах;
 *  - навигация (index.html) — network-first с откатом на кэш: свежий код
 *    при наличии сети, но не белый экран без неё;
 *  - API (chat/status/tree) — network-only: ответы агента нельзя кэшировать,
 *    иначе интерфейс покажет старый ответ. При обрыве связи приложение
 *    сообщает об этом самостоятельно.
 *
 * ВАЖНО: SW кэширует только статику. Данные агента (переписка, файлы)
 * живут на сервере и в Supabase — здесь их нет.
 */
const VERSION = 'claude-code-v7';
// Ресурсы, которые кладём в кэш при установке.
const ASSETS = [
  '/',
  '/static/app.css',
  '/static/app.js',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon-96.png',
  '/static/icons/icon-48.png',
  '/static/icons/favicon.ico',
];

self.addEventListener('install', (e) => {
  self.skipWaiting();
  e.waitUntil((async () => {
    const cache = await caches.open(VERSION);
    await Promise.all(ASSETS.map(async (url) => {
      try {
        await cache.add(new Request(url, { cache: 'reload' }));
      } catch (err) {
        console.warn('[sw] не закэширован', url, err);
      }
    }));
  })());
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    // Удаляем все старые версии кэша
    const keys = await caches.keys();
    await Promise.all(keys.map(k => {
      if (k !== VERSION) return caches.delete(k);
    }));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // API всегда идёт в сеть напрямую
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/admin/') ||
      url.pathname.startsWith('/health')) {
    return;
  }

  // Network-first для всех ресурсов, чтобы всегда загружался самый свежий код
  e.respondWith((async () => {
    try {
      const fresh = await fetch(req);
      if (fresh && fresh.ok) {
        const cache = await caches.open(VERSION);
        cache.put(req, fresh.clone());
      }
      return fresh;
    } catch (err) {
      const cache = await caches.open(VERSION);
      const cached = await cache.match(req);
      if (cached) return cached;
      if (req.mode === 'navigate') {
        return new Response(
          '<h1>Нет связи</h1><p>Откройте приложение при подключении к сети.</p>',
          { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
      }
      return new Response('', { status: 504, statusText: 'offline' });
    }
  })());
});