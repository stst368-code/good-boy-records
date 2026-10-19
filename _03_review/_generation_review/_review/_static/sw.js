const CACHE='gbr-shell-v1.9.0';
const SHELL=['/','/static/style.css','/static/base.css','/static/app.js','/static/tags.js','/static/icon.svg','/static/manifest.webmanifest','/tools/yaml-editor/editor.js','/tools/yaml-editor/editor.css','/tools/writing/writing.js','/tools/writing/writing.css','/tools/generation/generation.js','/tools/generation/workers.js','/tools/generation/generation.css','/tools/preparation/preparation.js','/tools/preparation/timeline.js','/tools/preparation/preparation.css'];
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(SHELL))));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))))));
self.addEventListener('fetch',event=>{
  if(event.request.method!=='GET'||new URL(event.request.url).pathname.startsWith('/api/'))return;
  event.respondWith(fetch(event.request).then(response=>{if(response.ok){const copy=response.clone();caches.open(CACHE).then(cache=>cache.put(event.request,copy));}return response;}).catch(()=>caches.match(event.request)));
});

