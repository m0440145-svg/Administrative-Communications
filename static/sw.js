// عامل خدمة مبسّط لتمكين تثبيت المنصة كتطبيق (PWA). لا يخزّن أي بيانات من الواجهة البرمجية أبدًا (بيانات حساسة)،
// ويخزّن فقط ملفات الواجهة الثابتة للعمل السريع، مع تفضيل الشبكة دائمًا لضمان أحدث نسخة.
const CACHE = "acp-shell-v4";
const SHELL = ["/", "/style.css", "/app.js", "/admin.js", "/ext.js", "/ext2.js", "/brand.js", "/icon.svg", "/manifest.json"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return; // لا تدخّل في الـ API
  e.respondWith(fetch(e.request).then((res) => {
    const copy = res.clone();
    if (res.ok) caches.open(CACHE).then((c) => c.put(e.request, copy));
    return res;
  }).catch(() => caches.match(e.request).then((r) => r || caches.match("/"))));
});
