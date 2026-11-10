import test from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes, createHash } from 'node:crypto';
import { buildSync } from 'esbuild';
import { Miniflare } from 'miniflare';
import { rm } from 'node:fs/promises';
import { request as httpRequest } from 'node:http';
import { FlowSocket } from '../src/flow-socket.mjs';

const origin = 'https://relay.test';
test('concurrent GET admission rejects before a second backend action', { timeout: 30000 }, async t => {
  buildSync({ entryPoints: ['src/worker.mjs'], outfile: 'dist/http-memory-worker.mjs', bundle: true, format: 'esm', platform: 'neutral' });
  t.after(() => rm('dist/http-memory-worker.mjs', { force: true }));
  const mf = new Miniflare({ workers: [{ name: 'relay', modules: true, scriptPath: 'dist/http-memory-worker.mjs', compatibilityDate: '2026-07-30', bindings: { ENROLLMENT_ENABLED: 'true', ALLOW_TEST_TIMEOUT: 'true', REQUEST_TIMEOUT_MS: '2000', MAX_HTTP_INFLIGHT: '1', HTTP_EXCHANGE_MB: '60', HTTP_QUEUE_WAIT_MS: '100' }, durableObjects: { INSTALLATIONS: { className: 'Installation', useSQLite: true }, DIRECTORY: { className: 'Directory', useSQLite: true }, GUARD: { className: 'Guard', useSQLite: true } } }] });
  t.after(() => mf.dispose());
  const id = randomBytes(16).toString('hex'), secret = randomBytes(32).toString('hex'), token = 'cdt_' + randomBytes(32).toString('hex');
  assert.equal((await mf.dispatchFetch(origin + '/v1/installations', { method: 'POST', body: JSON.stringify({ id, secret }) })).status, 201);
  const r = await mf.dispatchFetch(`${origin}/v1/installations/${id}/connect`, { headers: { Upgrade: 'websocket', Authorization: `Bearer ${secret}`, 'Sec-WebSocket-Protocol': 'clover-relay-connector-v2' } });
  assert.equal(r.status, 101); r.webSocket.accept(); const socket = new FlowSocket(r.webSocket); t.after(() => socket.close());
  let registered; const ready = new Promise(resolve => { registered = resolve; }); let actions = 0, first;
  const gotFirst = new Promise(resolve => { first = resolve; });
  socket.addEventListener('message', ({ data }) => { const m = JSON.parse(data); if (m.type === 'control.result') registered(m); if (m.type === 'http.request') { actions++; first(); } });
  socket.send(JSON.stringify({ type: 'devices.replace', id: 'control', devices: [{ hash: createHash('sha256').update(token).digest('hex'), expiresAt: Date.now() + 60000 }] }));
  assert.equal((await ready).ok, true);
  const id2 = randomBytes(16).toString('hex'), secret2 = randomBytes(32).toString('hex'), token2 = 'cdt_' + randomBytes(32).toString('hex');
  assert.equal((await mf.dispatchFetch(origin + '/v1/installations', { method: 'POST', body: JSON.stringify({ id: id2, secret: secret2 }) })).status, 201);
  const connected2 = await mf.dispatchFetch(`${origin}/v1/installations/${id2}/connect`, { headers: { Upgrade: 'websocket', Authorization: `Bearer ${secret2}`, 'Sec-WebSocket-Protocol': 'clover-relay-connector-v2' } });
  assert.equal(connected2.status, 101); connected2.webSocket.accept(); const socket2 = new FlowSocket(connected2.webSocket); t.after(() => socket2.close());
  let registered2; const ready2 = new Promise(resolve => { registered2 = resolve; });
  socket2.addEventListener('message', ({ data }) => { const m = JSON.parse(data); if (m.type === 'control.result') registered2(m); if (m.type === 'http.request') actions++; });
  socket2.send(JSON.stringify({ type: 'devices.replace', id: 'control', devices: [{ hash: createHash('sha256').update(token2).digest('hex'), expiresAt: Date.now() + 60000 }] }));
  assert.equal((await ready2).ok, true);
  const headers = { Authorization: `Bearer ${token}` };
  let unblock; const held = new Promise(resolve => { unblock = resolve; });
  // Emit a first byte so the HTTP client actually transmits headers before stalling.
  const body = new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array([1])); }, async pull(controller) { await held; controller.close(); } });
  const upload = mf.dispatchFetch(origin + '/api/audio/transcribe', { method: 'POST', headers, body, duplex: 'half' });
  await new Promise(resolve => setTimeout(resolve, 100));
  const busy = await mf.dispatchFetch(origin + '/api/sessions', { headers });
  const excess = await Promise.all(Array.from({ length: 32 }, (_, i) => {
    const bytes = new Uint8Array([i, 1, 2, 3]);
    const stream = new ReadableStream({ start(controller) { controller.enqueue(bytes); controller.close(); } });
    return mf.dispatchFetch(origin + '/api/audio/transcribe', {
      method: 'POST', headers: i % 2 ? headers : { ...headers, 'content-length': '4' },
      body: stream, duplex: 'half'
    });
  }));
  assert.deepEqual(excess.map(response => response.status), Array(32).fill(503));
  assert.equal(actions, 0, 'excess uploads do not reach the backend');
  const networkOrigin = (await mf.ready).origin;
  const networkStatuses = await Promise.all(Array.from({ length: 32 }, (_, i) => new Promise((resolve, reject) => {
    let settled = false;
    const auth = i % 2 ? { Authorization: `Bearer ${token2}` } : headers;
    const req = httpRequest(networkOrigin + '/api/audio/transcribe', { method: 'POST', headers: i % 4 < 2 ? auth : { ...auth, 'content-length': '4' }, timeout: 3000 }, res => {
      settled = true; resolve(res.statusCode); res.resume(); req.destroy();
    });
    req.on('timeout', () => req.destroy(Error('overload deadline')));
    req.on('error', error => { if (!settled) reject(error); });
    req.end(Buffer.alloc(i % 4 < 2 ? 1 : 4, i));
  })));
  assert.deepEqual(networkStatuses, Array(32).fill(503));
  assert.equal(actions, 0, 'network overload does not reach the backend');
  unblock();
  assert.equal(busy.status, 503, 'admission is acquired before an upload body is read');
  await gotFirst; assert.equal((await upload).status, 504);
  first = () => {}; actions = 0;
  const pending = mf.dispatchFetch(origin + '/api/sessions', { headers });
  await new Promise(resolve => setTimeout(resolve, 100));
  const second = await mf.dispatchFetch(origin + '/api/sessions', { headers });
  assert.equal(second.status, 503); assert.equal(actions, 1);
  assert.equal((await pending).status, 504);
});
