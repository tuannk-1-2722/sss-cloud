const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');

const streamSource = fs.readFileSync('backend/security_app/static/camera-stream.js', 'utf8');
const appSource = fs.readFileSync('backend/security_app/static/app.js', 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const encoder = new TextEncoder();
function part(id, age = 0, jpeg = new Uint8Array([255, 216, 255, 217]), revision = 2) {
  const head = encoder.encode(`--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${jpeg.length}\r\nX-Frame-Id: ${id}\r\nX-Frame-Age-Ms: ${age}\r\nX-Video-Revision: ${revision}\r\n\r\n`);
  return Buffer.concat([head, jpeg, Buffer.from('\r\n')]);
}

function realm(withApp = false) {
  let now = 0, serial = 0, responseStatus = 200;
  const timers = new Map(), requests = [], streams = [], elements = new Map(), listeners = {};
  const strokes = [];
  const context2d = new Proxy({}, {get: (_, key) => (...args) => {
    if (key === 'strokeRect') strokes.push(args);
  }, set: () => true});
  function element(id) {
    if (elements.has(id)) return elements.get(id);
    const item = {
      hidden: id.endsWith('-image'), value: id === 'severity-filter' ? 'all' : '',
      naturalWidth: 640, naturalHeight: 480, complete: true, children: [],
      classList: {add() {}, remove() {}, toggle() {}}, dataset: {},
      append(...nodes) { this.children.push(...nodes); },
      replaceChildren(...nodes) { this.children = nodes; },
      removeAttribute(name) { if (name === 'src') this._src = this.currentSrc = ''; },
      addEventListener() {}, getContext: () => context2d,
    };
    Object.defineProperty(item, 'src', {
      get: () => item._src || '',
      set(value) {
        item._src = item.currentSrc = value;
        const handler = item.onload;
        if (handler) queueMicrotask(() => handler());
      },
    });
    elements.set(id, item);
    return item;
  }
  const nav = ['monitor', 'people', 'history'].map(name => {
    const item = element('nav-' + name); item.dataset.tab = name; return item;
  });
  let status = {
    mode: 'DISARMED', mode_revision: 2, profile: 'hardware', device_online: true,
    vision_ready: true, active_people: 0, frames_replaced: 0, tracks: [], frame_age_ms: 0,
    capture: {active: false},
    enrollment_requirements: {per_round_min: 5, accepted_min: 10, max_people: 20, face_region: [0.15,0.05,0.7,0.9]},
    ui_limits: {preview_ms: 30000, test_cap_ms: 1000, evidence_days: 7},
    video: {received_fps: 8}, analysis: {interval_ms: 300, effective_fps: 3.3, duration_p95_ms: null},
    zones: {approach: [0,0,0.4,1], doorstep: [0.6,0,0.4,1]},
  };
  let statusFailure = false, mutationReply = null;
  const jsonResponse = (data, code = 200) => ({status: code, ok: code < 400, json: async () => data});
  const sandbox = {
    TextDecoder, TextEncoder, Uint8Array, Blob, AbortController,
    Image: function() { return element('probe-' + (++serial)); },
    performance: {now: () => now}, Date, URLSearchParams,
    URL: {createObjectURL: () => 'blob:fixture-' + (++serial), revokeObjectURL() {}},
    sessionStorage: {getItem: () => '', setItem() {}, removeItem() {}},
    location: {search: '', protocol: 'http:', hostname: 'fixture.invalid'}, navigator: {},
    document: {hidden: false, documentElement: {}, getElementById: element,
      querySelectorAll: selector => selector === 'nav button' ? nav : [],
      createElement: () => element('node-' + (++serial)),
      addEventListener: (name, handler) => { listeners[name] = handler; }},
    window: {addEventListener: (name, handler) => { listeners[name] = handler; }},
    getComputedStyle: () => ({getPropertyValue: () => '#00aaaa'}),
    setTimeout: (fn, delay) => { const id = ++serial; timers.set(id, {fn, at: now + delay}); return id; },
    clearTimeout: id => timers.delete(id), setInterval: () => ++serial,
    fetch: async (url, options = {}) => {
      requests.push({url, method: options.method || 'GET', signal: options.signal, body: options.body, headers: options.headers});
      if (url.includes('/live.mjpg')) {
        const code = responseStatus;
        let controller;
        const body = new ReadableStream({start(value) { controller = value; }});
        options.signal.addEventListener('abort', () => {
          try { controller.error(new Error('AbortError')); } catch (_) { /* closed fixture */ }
        }, {once: true});
        streams.push(controller);
        return {status: code, ok: code === 200, body,
          headers: {get: () => 'multipart/x-mixed-replace; boundary=frame'}};
      }
      if (url.endsWith('/video/viewers')) return jsonResponse({lease_id: 'fixture-view', remaining_ms: 15000});
      if (url.endsWith('/status')) {
        if (statusFailure) throw new Error('offline fixture');
        return jsonResponse(status);
      }
      if (url.endsWith('/capture') && options.method === 'DELETE' && mutationReply) return mutationReply;
      return jsonResponse([]);
    },
  };
  const context = vm.createContext(sandbox);
  vm.runInContext(streamSource, context);
  if (withApp) vm.runInContext(appSource, context);
  return {
    context, element, requests, streams, timers, listeners, strokes, nav,
    run: code => vm.runInContext(code, context),
    setStatus: value => { status = value; }, status: () => status,
    setFailure: value => { statusFailure = value; },
    setResponse: code => { responseStatus = code; },
    setMutation: value => { mutationReply = value; },
    advance: async ms => {
      now += ms;
      // Snapshot only: callbacks may schedule future retries, never run them early.
      for (const [id, timer] of [...timers]) if (timer.at <= now && timers.has(id)) {
        timers.delete(id); timer.fn();
      }
      await flush();
    },
  };
}

test('multipart parsing survives every byte split, combined frames and boundary-like JPEG bytes', () => {
  const r = realm(); r.context.frames = [];
  r.run('parser = new CameraMultipartReader(frame => frames.push(frame))');
  const jpeg = encoder.encode('binary\r\n--frame\r\nbytes');
  const data = Buffer.concat([part('frame-1', 12, jpeg), part('frame-2', 99)]);
  for (const value of data) { r.context.chunk = new Uint8Array([value]); r.run('parser.push(chunk)'); }
  assert.deepEqual(Array.from(r.context.frames, frame => frame.id), ['frame-1', 'frame-2']);
  assert.deepEqual(Array.from(r.context.frames[0].jpeg), Array.from(jpeg));
  r.context.chunk = Buffer.concat([part('frame-3'), part('frame-4')]); r.run('parser.push(chunk)');
  assert.equal(r.context.frames.length, 4);
});

test('multipart reader bounds headers/payloads and rejects missing identity or expired age', () => {
  for (const data of [
    Buffer.alloc(5000, 65),
    encoder.encode('--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 524289\r\nX-Frame-Id: x\r\nX-Frame-Age-Ms: 0\r\n\r\n'),
    encoder.encode('--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\n'),
    part('old', 5001),
  ]) {
    const r = realm(); r.context.chunk = data;
    assert.throws(() => r.run('new CameraMultipartReader(() => {}).push(chunk)'));
  }
});

const videoRequests = r => r.requests.filter(x => x.url.includes('/live.mjpg'));
const startStream = r => r.run('stream = new CameraStream({onFrame: f => seen.push(f.id), onClear: () => clears++, onState() {}, onUnauthorized: () => unauthorized++, getCsrf: () => "csrf-fixture"}); stream.start("monitor"); stream.start("monitor")');

test('stream creates one renewable view, renders frames, expires by age and stops without retries', async () => {
  const r = realm(); Object.assign(r.context, {seen: [], clears: 0, unauthorized: 0});
  startStream(r); await flush();
  assert.equal(videoRequests(r).length, 1);
  assert.equal(r.requests.find(x => x.url.endsWith('/video/viewers')).headers['X-CSRF-Token'], 'csrf-fixture');
  r.streams[0].enqueue(part('frame-1', 2900)); await flush();
  assert.deepEqual(Array.from(r.context.seen), ['frame-1']);
  await r.advance(101); assert.ok(videoRequests(r)[0].signal.aborted);
  r.run('stream.stop()'); await r.advance(10000);
  assert.equal(videoRequests(r).length, 1);
  assert.ok(r.requests.some(x => x.method === 'DELETE' && x.url.includes('/video/viewers/')));
});

test('normal EOF reconnects on same lease; 401 clears authentication', async () => {
  const r = realm(); Object.assign(r.context, {seen: [], clears: 0, unauthorized: 0});
  startStream(r); await flush(); r.streams[0].close(); await flush();
  await r.advance(249); assert.equal(videoRequests(r).length, 1);
  r.setResponse(401); await r.advance(1);
  assert.equal(videoRequests(r).length, 2); assert.equal(r.context.unauthorized, 1);
  await r.advance(10000); assert.equal(videoRequests(r).length, 2);
});

test('a new video generation clears old display and accepts new frames without mode gating', async () => {
  const r = realm(); Object.assign(r.context, {seen: [], clears: 0, unauthorized: 0});
  startStream(r); await flush();
  r.streams[0].enqueue(part('matching')); await flush();
  const before = r.context.clears;
  r.streams[0].enqueue(part('new-boot', 0, undefined, 3)); await flush();
  assert.deepEqual(Array.from(r.context.seen), ['matching', 'new-boot']);
  assert.ok(r.context.clears > before); r.run('stream.stop()');
});

test('malformed frames and quiet camera time out; 429 reconnect uses backoff', async () => {
  const r = realm(); Object.assign(r.context, {seen: [], clears: 0, unauthorized: 0});
  startStream(r); await flush(); r.streams[0].enqueue(part('too-old', 6000)); await flush();
  assert.ok(r.context.clears >= 2);
  r.setResponse(429); await r.advance(1000);
  const count = videoRequests(r).length;
  await r.advance(1999); assert.equal(videoRequests(r).length, count);
  await r.advance(1); assert.equal(videoRequests(r).length, count + 1);
  r.run('stream.stop()');
  const empty = realm(); Object.assign(empty.context, {seen: [], clears: 0, unauthorized: 0});
  startStream(empty); await flush(); await empty.advance(3001);
  assert.ok(videoRequests(empty)[0].signal.aborted); empty.run('stream.stop()');
});

test('DISARMED automatically renders live frames; ARM/DISARM keep the same connection', async () => {
  const r = realm(true); r.run('csrf = "fixture"'); await r.run('refresh()'); await flush();
  assert.equal(videoRequests(r).length, 1);
  for (const id of ['frame-1', 'frame-2', 'frame-3']) {
    r.streams[0].enqueue(part(id)); await flush(); await r.advance(250);
  }
  assert.equal(r.run('displayedFrame'), 'frame-3');
  assert.equal(r.element('camera-image').hidden, false);
  assert.equal(r.requests.filter(x => x.url.includes('latest-frame') || x.url.includes('capture/preview')).length, 0);
  r.status().mode = 'ARMED'; r.status().mode_revision++;
  await r.run('refresh()'); await flush();
  r.status().mode = 'DISARMED'; r.status().mode_revision++;
  await r.run('refresh()'); await flush();
  assert.equal(videoRequests(r).length, 1);
  assert.ok(!videoRequests(r)[0].signal.aborted);
  r.run('invalidateCamera()');
});

test('enrollment uses the same source with its own viewer demand and exact-ID AI overlays', async () => {
  const r = realm(true); r.run('csrf = "fixture"'); await r.run('refresh()'); await flush();
  r.streams[0].enqueue(part('frame-1')); await flush();
  r.status().latest_analysis = {frame_id: 'other-frame', observations: [{bbox: [0,0,1,1], decision: 'UNKNOWN'}]};
  r.strokes.length = 0; r.run('drawMonitorOverlay()'); assert.equal(r.strokes.length, 2);
  r.status().latest_analysis.frame_id = 'frame-1';
  r.strokes.length = 0; r.run('drawMonitorOverlay()'); assert.equal(r.strokes.length, 3);
  r.status().mode = 'ENROLLMENT'; r.status().mode_revision++;
  r.status().capture = {active: true, id: 'enrollment-1', purpose: 'enrollment', remaining_ms: 600000, accepted: [0,0], round: 1, rejected: 0};
  r.nav[1].onclick(); await flush();
  assert.ok(videoRequests(r)[0].signal.aborted);
  assert.equal(videoRequests(r).length, 2);
  const renewal = r.requests.filter(x => x.url.endsWith('/video/viewers')).at(-1);
  assert.equal(JSON.parse(renewal.body).enrollment_session_id, 'enrollment-1');
  r.streams[1].enqueue(part('enrolled-frame')); await flush();
  assert.equal(r.element('camera-image').src, '');
  assert.equal(r.element('enrollment-image').hidden, false);
  r.nav[2].onclick(); assert.equal(r.element('enrollment-image').src, '');
});

test('hidden/logout clear images, abort stream and release view; status fault does not stop valid video', async () => {
  for (const reason of ['hidden', 'logout', 'status-failure']) {
    const r = realm(true); r.run('csrf = "fixture"'); await r.run('refresh()'); await flush();
    r.streams[0].enqueue(part('fresh')); await flush();
    if (reason === 'hidden') { r.context.document.hidden = true; r.listeners.visibilitychange(); }
    if (reason === 'logout') r.run('logoutView()');
    if (reason === 'status-failure') {
      r.setFailure(true); await r.run('refresh()');
      assert.equal(r.element('camera-image').hidden, false);
      assert.ok(!videoRequests(r)[0].signal.aborted);
      r.run('logoutView()');
    }
    assert.equal(r.element('camera-image').src, '');
    assert.ok(videoRequests(r)[0].signal.aborted);
    assert.ok(r.requests.some(x => x.method === 'DELETE'));
  }
});

test('decode work is bounded to one running and one newest candidate, with generation guard', async () => {
  const r = realm(); r.context.painted = []; r.context.decoded = []; r.context.releases = [];
  r.run('renderer = new LatestFrameRenderer(f => new Promise(resolve => { decoded.push(f); releases.push(() => resolve({dispose() {}})); }), f => painted.push(f)); renderer.submit("A"); renderer.submit("B"); renderer.submit("C"); renderer.submit("D")');
  assert.deepEqual(Array.from(r.context.decoded), ['A']);
  r.context.releases.shift()(); await flush();
  assert.deepEqual(Array.from(r.context.decoded), ['A', 'D']);
  r.run('renderer.clear()'); r.context.releases.shift()(); await flush();
  assert.deepEqual(Array.from(r.context.painted), ['A']);
});
