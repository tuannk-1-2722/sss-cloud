/* Static shell only. Never cache login, camera frames, events or biometric evidence. */
const SHELL = "sss-static-v11";
const ASSETS = ["/static/app.css", "/static/camera-stream.js", "/static/app.js", "/static/icon-192.png", "/static/icon-512.png", "/manifest.webmanifest"];
self.addEventListener("install", event => {
  event.waitUntil(caches.open(SHELL).then(cache => cache.addAll(ASSETS)));
  self.skipWaiting();
});
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== SHELL).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin || !ASSETS.includes(url.pathname)) return;
  event.respondWith(fetch(event.request).then(response => {
    if (response.ok) caches.open(SHELL).then(cache => cache.put(event.request, response.clone()));
    return response;
  }).catch(() => caches.match(event.request)));
});
self.addEventListener("push", event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = {}; }
  const text = (value, fallback, max) => typeof value === "string" && value ? value.slice(0, max) : fallback;
  const options = {
    body: text(data.body, "", 200),
    tag: text(data.event_id, "sss-event", 64),
    renotify: true,
    requireInteraction: true,
    icon: "/static/icon-192.png",
    badge: "/static/icon-192.png",
    data: { url: "/?live=1" }
  };
  // Only our own short-lived evidence link may be shown; anything else is dropped.
  if (typeof data.image === "string") {
    try {
      const image = new URL(data.image);
      if (image.origin === self.location.origin && image.pathname.startsWith("/push-media/")) options.image = image.href;
    } catch (e) { /* ignore malformed image */ }
  }
  event.waitUntil(self.registration.showNotification(text(data.title, "Smart Security", 120), options));
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  const target = new URL("/?live=1", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(windows => {
    for (const client of windows) {
      if (new URL(client.url).origin === self.location.origin && "focus" in client) return client.focus();
    }
    return self.clients.openWindow(target);
  }));
});
