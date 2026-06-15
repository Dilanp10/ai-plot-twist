/// <reference lib="webworker" />
declare const self: ServiceWorkerGlobalScope;

import { clientsClaim } from 'workbox-core';
import { cleanupOutdatedCaches, precacheAndRoute } from 'workbox-precaching';
import { registerRoute } from 'workbox-routing';
import { CacheFirst, NetworkOnly, StaleWhileRevalidate } from 'workbox-strategies';
import { ExpirationPlugin } from 'workbox-expiration';
import { CacheableResponsePlugin } from 'workbox-cacheable-response';

// ---------------------------------------------------------------------------
// Workbox precaching (replaces generateSW's auto-precache)
// ---------------------------------------------------------------------------

// __WB_MANIFEST is injected by vite-plugin-pwa at build time.
precacheAndRoute(self.__WB_MANIFEST);
cleanupOutdatedCaches();

// autoUpdate behaviour: claim all clients immediately on activation.
self.skipWaiting();
clientsClaim();

// ---------------------------------------------------------------------------
// Runtime caching (ported from vite.config.ts workbox.runtimeCaching)
// ---------------------------------------------------------------------------

registerRoute(
  ({ url }) => url.pathname === '/api/v1/chapters/today',
  new StaleWhileRevalidate({
    cacheName: 'api-chapters-today',
    plugins: [new ExpirationPlugin({ maxAgeSeconds: 10 * 60, maxEntries: 8 })],
  }),
);

registerRoute(
  ({ url }) => url.pathname.startsWith('/api/v1/seasons/'),
  new StaleWhileRevalidate({
    cacheName: 'api-seasons',
    plugins: [new ExpirationPlugin({ maxAgeSeconds: 60 * 60, maxEntries: 16 })],
  }),
);

registerRoute(
  ({ url }) => url.host === 'assets.aiplottwist.example',
  new CacheFirst({
    cacheName: 'r2-assets',
    plugins: [
      new ExpirationPlugin({ maxAgeSeconds: 30 * 24 * 60 * 60, maxEntries: 200 }),
      new CacheableResponsePlugin({ statuses: [0, 200] }),
    ],
  }),
);

registerRoute(
  ({ url }) => url.pathname.startsWith('/api/'),
  new NetworkOnly(),
);

// ---------------------------------------------------------------------------
// Push notification handler (module 011 / T-013, FR-012)
// ---------------------------------------------------------------------------

interface PushPayload {
  title?: string;
  body?: string;
  url?: string;
  icon?: string;
}

self.addEventListener('push', (event: PushEvent) => {
  let payload: PushPayload = {};
  try {
    payload = (event.data?.json() as PushPayload) ?? {};
  } catch {
    // Malformed JSON — show a generic notification.
  }

  const title = payload.title ?? 'AI Plot Twist';
  const options: NotificationOptions = {
    body: payload.body ?? 'Hay novedades en la historia.',
    icon: payload.icon ?? '/icons/icon-192.png',
    badge: '/icons/icon-192.png',
    data: { url: payload.url ?? '/' },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

// ---------------------------------------------------------------------------
// Notification click handler (FR-013)
// ---------------------------------------------------------------------------

self.addEventListener('notificationclick', (event: NotificationEvent) => {
  event.notification.close();

  const targetUrl: string =
    (event.notification.data as { url?: string })?.url ?? '/';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if (new URL(client.url).pathname === targetUrl && 'focus' in client) {
          return client.focus();
        }
      }
      return self.clients.openWindow(targetUrl);
    }),
  );
});
