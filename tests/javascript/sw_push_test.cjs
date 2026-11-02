const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');

const source = fs.readFileSync('backend/security_app/static/sw.js', 'utf8');

function worker() {
  const listeners = {}, shown = [], opened = [], focused = [];
  let windows = [];
  const self = {
    location: {origin: 'https://home.example.org'},
    addEventListener: (name, fn) => { listeners[name] = fn; },
    skipWaiting() {},
    registration: {showNotification: (title, options) => { shown.push({title, options}); return Promise.resolve(); }},
    clients: {
      claim() {},
      matchAll: async () => windows,
      openWindow: async url => { opened.push(url); }
    }
  };
  vm.runInNewContext(source, {self, URL, caches: {}, fetch() {}});
  return {
    shown, opened, focused, listeners,
    setWindows: list => { windows = list.map(url => ({url, focus: async () => { focused.push(url); }})); },
    async push(data, raw) {
      let pending;
      listeners.push({data: {json: () => { if (raw) throw new Error('bad'); return data; }}, waitUntil: p => { pending = p; }});
      await pending;
    },
    async click() {
      let pending, closed = false;
      listeners.notificationclick({notification: {close: () => { closed = true; }}, waitUntil: p => { pending = p; }});
      await pending;
      assert.ok(closed);
    }
  };
}

test('push shows a same-origin evidence image and a capped text body', async () => {
  const w = worker();
  const image = 'https://home.example.org/push-media/abc?exp=1&sig=2';
  await w.push({title: 'CẢNH BÁO', body: 'x'.repeat(500), event_id: 'e1', image});
  const {title, options} = w.shown[0];
  assert.equal(title, 'CẢNH BÁO');
  assert.equal(options.body.length, 200);
  assert.equal(options.image, image);
  assert.equal(options.tag, 'e1');
  assert.equal(options.data.url, '/?live=1');
});

test('push drops foreign, non-evidence or malformed image URLs and survives bad payloads', async () => {
  const w = worker();
  for (const image of ['https://evil.example.org/push-media/a', 'https://home.example.org/api/x', 'not a url', 42]) {
    await w.push({title: 't', image});
  }
  await w.push(null, true);
  await w.push(null);
  await w.push([]);
  assert.equal(w.shown.length, 7);
  assert.ok(w.shown.every(item => item.options.image === undefined));
  assert.equal(w.shown[4].title, 'Smart Security');
});

test('notification click focuses an existing window or opens the dashboard, never payload URLs', async () => {
  const w = worker();
  await w.click();
  assert.deepEqual(w.opened, ['https://home.example.org/?live=1']);
  w.setWindows(['https://home.example.org/']);
  await w.click();
  assert.deepEqual(w.focused, ['https://home.example.org/']);
  assert.equal(w.opened.length, 1);
});
