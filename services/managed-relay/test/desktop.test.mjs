import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:http';
import { createHash, randomBytes } from 'node:crypto';
import { buildSync } from 'esbuild';
import { Miniflare } from 'miniflare';
import WebSocket, { WebSocketServer } from 'ws';
import { loadOrCreateIdentity, ensureEnrolled, openConnector } from '../src/desktop-connector.mjs';
import { FlowSocket } from '../src/flow-socket.mjs';

test('desktop persists before enrollment and forwards to its loopback backend', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'relay-desktop-')); t.after(() => rm(dir, { recursive: true, force: true }));
  const file = join(dir, 'identity.json');
  const [identity, same] = await Promise.all([loadOrCreateIdentity(file), loadOrCreateIdentity(file)]);
  assert.deepEqual(identity, same);
  assert.equal((await stat(file)).mode & 0o777, 0o600);
  assert.deepEqual(JSON.parse(await readFile(file, 'utf8')), identity);
  buildSync({ entryPoints: ['src/worker.mjs'], outfile: 'dist/worker.mjs', bundle: true, format: 'esm', platform: 'neutral' });
  const mf = new Miniflare({ workers: [{ name: 'relay', modules: true, scriptPath: 'dist/worker.mjs', compatibilityDate: '2026-07-30', bindings: { ENROLLMENT_ENABLED: 'true' }, durableObjects: { INSTALLATIONS: { className: 'Installation', useSQLite: true }, DIRECTORY: { className: 'Directory', useSQLite: true }, GUARD: { className: 'Guard', useSQLite: true } } }] });
  t.after(() => mf.dispose()); const origin = (await mf.ready).origin;
  assert.equal((await ensureEnrolled(origin, file, { allowInsecureLocalhost: true })).status, 201);
  assert.equal((await ensureEnrolled(origin, file, { allowInsecureLocalhost: true })).status, 200);
  const token = 'cdt_' + randomBytes(32).toString('hex');
  const backend = createServer((req, res) => { res.writeHead(req.headers.authorization === `Bearer ${token}` ? 200 : 401, { 'content-type': 'application/json' }); res.end(JSON.stringify({ handled: 'local', host: req.headers.host, auth: req.headers.authorization, target: req.url, method: req.method, spoof: req.headers['x-relay-target'] })); });
  await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve)); t.after(() => new Promise(resolve => backend.close(resolve)));
  const connector = await openConnector({ relayOrigin: origin, identity, backendOrigin: `http://127.0.0.1:${backend.address().port}`, allowInsecureLocalhost: true });
  t.after(() => connector.close());
  const hash = createHash('sha256').update(token).digest('hex');
  await connector.replaceDevices([{ hash, expiresAt: Date.now() + 60000 }]);
  const response = await fetch(origin + '/api/sessions', { headers: { Authorization: `Bearer ${token}`, 'x-relay-method': 'DELETE', 'x-relay-target': '/api/admin', 'x-relay-device-hash': 'attacker' } });
  assert.equal(response.status, 200); const payload = await response.json();
  assert.equal(payload.handled, 'local'); assert.equal(payload.target, '/api/sessions'); assert.equal(payload.method, 'GET'); assert.equal(payload.spoof, undefined);
});

test('closing connector aborts an in-flight backend HTTP request', { timeout: 10000 }, async t => {
  buildSync({ entryPoints: ['src/worker.mjs'], outfile: 'dist/worker.mjs', bundle: true, format: 'esm', platform: 'neutral' });
  const mf = new Miniflare({ workers: [{ name: 'relay', modules: true, scriptPath: 'dist/worker.mjs', compatibilityDate: '2026-07-30', bindings: { ENROLLMENT_ENABLED: 'true' }, durableObjects: { INSTALLATIONS: { className: 'Installation', useSQLite: true }, DIRECTORY: { className: 'Directory', useSQLite: true }, GUARD: { className: 'Guard', useSQLite: true } } }] });
  t.after(() => mf.dispose()); const origin = (await mf.ready).origin;
  const dir = await mkdtemp(join(tmpdir(), 'relay-close-')); t.after(() => rm(dir, { recursive: true, force: true }));
  const { identity } = await ensureEnrolled(origin, join(dir, 'identity'), { allowInsecureLocalhost: true });
  const token = 'cdt_' + randomBytes(32).toString('hex'); let arrived, disconnected;
  const started = new Promise(resolve => { arrived = resolve; });
  const aborted = new Promise(resolve => { disconnected = resolve; });
  const backend = createServer((req, res) => { arrived(); res.on('close', disconnected); });
  await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve)); t.after(() => { backend.closeAllConnections(); backend.close(); });
  const connector = await openConnector({ relayOrigin: origin, identity, backendOrigin: `http://127.0.0.1:${backend.address().port}`, allowInsecureLocalhost: true });
  t.after(() => connector.close());
  await connector.replaceDevices([{ hash: createHash('sha256').update(token).digest('hex'), expiresAt: Date.now() + 60000 }]);
  const pending = fetch(origin + '/api/sessions', { headers: { Authorization: `Bearer ${token}` } }).catch(() => null);
  await started; connector.close();
  await Promise.race([aborted, new Promise((_, reject) => setTimeout(() => reject(Error('backend request not aborted')), 1000))]);
  await pending;
});

test('close after HTTP admission prevents dispatch and reconnect never replays it', { timeout: 15000 }, async t => {
  buildSync({ entryPoints: ['src/worker.mjs'], outfile: 'dist/close-race-worker.mjs', bundle: true, format: 'esm', platform: 'neutral' });
  t.after(() => rm('dist/close-race-worker.mjs', { force: true }));
  const mf = new Miniflare({ workers: [{ name: 'relay', modules: true, scriptPath: 'dist/close-race-worker.mjs', compatibilityDate: '2026-07-30', bindings: { ENROLLMENT_ENABLED: 'true' }, durableObjects: { INSTALLATIONS: { className: 'Installation', useSQLite: true }, DIRECTORY: { className: 'Directory', useSQLite: true }, GUARD: { className: 'Guard', useSQLite: true } } }] });
  t.after(() => mf.dispose()); const origin = (await mf.ready).origin;
  const dir = await mkdtemp(join(tmpdir(), 'relay-dispatch-race-')); t.after(() => rm(dir, { recursive: true, force: true }));
  const { identity } = await ensureEnrolled(origin, join(dir, 'identity'), { allowInsecureLocalhost: true });
  const token = 'cdt_' + randomBytes(32).toString('hex');
  let backendActions = 0;
  const backend = createServer((_req, res) => { backendActions++; res.end('ok'); });
  await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve));
  t.after(() => { backend.closeAllConnections(); backend.close(); });
  const options = { relayOrigin: origin, identity, backendOrigin: `http://127.0.0.1:${backend.address().port}`, allowInsecureLocalhost: true };
  const original = FlowSocket.prototype.addEventListener;
  let connector, observedAdmission = false;
  // Test-only scheduling hook: run the real listener through job admission,
  // then close in its microtask continuation, before setImmediate dispatch.
  FlowSocket.prototype.addEventListener = function(type, listener, options) {
    if (type !== 'message') return original.call(this, type, listener, options);
    return original.call(this, type, async event => {
      await listener(event);
      if (typeof event.data === 'string' && JSON.parse(event.data).type === 'http.request') {
        observedAdmission = true; connector.close();
      }
    }, options);
  };
  t.after(() => { FlowSocket.prototype.addEventListener = original; });
  connector = await openConnector(options); t.after(() => connector.close());
  FlowSocket.prototype.addEventListener = original;
  const devices = [{ hash: createHash('sha256').update(token).digest('hex'), expiresAt: Date.now() + 60000 }];
  await connector.replaceDevices(devices);
  const headers = { Authorization: `Bearer ${token}` };
  const failed = await fetch(origin + '/api/sessions', { headers, signal: AbortSignal.timeout(5000) });
  assert.equal(failed.status, 503); await failed.arrayBuffer();
  assert.equal(observedAdmission, true); assert.equal(backendActions, 0);
  const replacement = await openConnector(options); t.after(() => replacement.close());
  await replacement.replaceDevices(devices);
  const healthy = await fetch(origin + '/api/sessions', { headers, signal: AbortSignal.timeout(5000) });
  assert.equal(healthy.status, 200); assert.equal(await healthy.text(), 'ok');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(backendActions, 1, 'only the explicitly new request executes after reconnect');
});

test('standalone connector relays local WebSocket text and binary', { timeout: 10000 }, async t => {
  buildSync({ entryPoints: ['src/worker.mjs'], outfile: 'dist/worker.mjs', bundle: true, format: 'esm', platform: 'neutral' });
  const mf = new Miniflare({ workers: [{ name: 'relay', modules: true, scriptPath: 'dist/worker.mjs', compatibilityDate: '2026-07-30', bindings: { ENROLLMENT_ENABLED: 'true', REQUEST_TIMEOUT_MS: '1000', ALLOW_TEST_TIMEOUT: 'true' }, durableObjects: { INSTALLATIONS: { className: 'Installation', useSQLite: true }, DIRECTORY: { className: 'Directory', useSQLite: true }, GUARD: { className: 'Guard', useSQLite: true } } }] });
  t.after(() => mf.dispose()); const origin = (await mf.ready).origin;
  const dir = await mkdtemp(join(tmpdir(), 'relay-ws-')); t.after(() => rm(dir, { recursive: true, force: true }));
  const { identity } = await ensureEnrolled(origin, join(dir, 'identity'), { allowInsecureLocalhost: true });
  const token = 'cdt_' + randomBytes(32).toString('hex'), ticket = randomBytes(32).toString('base64url');
  const backend = createServer((req, res) => { res.writeHead(req.headers.authorization === `Bearer ${token}` ? 200 : 401, { 'content-type': 'application/json' }); res.end(JSON.stringify({ ticket, ttl_seconds: 30 })); });
  let wrongProtocol = false, upgradeMode = 'normal';
  const pendingSockets = new Set();
  const wss = new WebSocketServer({ noServer: true, handleProtocols: protocols => wrongProtocol ? `clover-gateway-ticket.${ticket}` : protocols.has('clover-gateway-v1') ? 'clover-gateway-v1' : false });
  backend.on('upgrade', (req, socket, head) => {
    if (req.url !== '/api/ws' || req.headers.authorization || !req.headers['sec-websocket-protocol']?.includes(`clover-gateway-ticket.${ticket}`)) return socket.destroy();
    if (upgradeMode === 'failed') return socket.destroy();
    if (upgradeMode === 'invalid') return socket.end('HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n');
    if (upgradeMode === 'slow') { pendingSockets.add(socket); socket.once('close', () => pendingSockets.delete(socket)); return; }
    wss.handleUpgrade(req, socket, head, ws => wss.emit('connection', ws));
  });
  t.after(() => { for (const socket of pendingSockets) socket.destroy(); });
  wss.on('connection', ws => ws.on('message', (data, binary) => { if (!binary && String(data) === 'BIG') ws.send('x'.repeat(9 * 1024 * 1024)); else ws.send(data, { binary }); }));
  await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve)); t.after(() => new Promise(resolve => { for (const ws of wss.clients) ws.terminate(); backend.close(resolve); }));
  const connector = await openConnector({ relayOrigin: origin, identity, backendOrigin: `http://127.0.0.1:${backend.address().port}`, allowInsecureLocalhost: true }); t.after(() => connector.close());
  await connector.replaceDevices([{ hash: createHash('sha256').update(token).digest('hex'), expiresAt: Date.now() + 60000 }]);
  const minted = await fetch(origin + '/api/auth/ws-ticket', { method: 'POST', headers: { Authorization: `Bearer ${token}` } }); assert.equal(minted.status, 200);
  const dial = new URL('/api/ws', origin); dial.protocol = 'ws:';
  const rawPhone = new WebSocket(dial, ['clover-relay-flow-v1', 'clover-gateway-v1', `clover-gateway-ticket.${(await minted.json()).ticket}`], { handshakeTimeout: 3000, maxPayload: 23000 }); t.after(() => rawPhone.terminate());
  await new Promise((resolve, reject) => { rawPhone.once('open', resolve); rawPhone.once('error', reject); }); assert.equal(rawPhone.protocol, 'clover-relay-flow-v1');
  const phone = new FlowSocket(rawPhone, { maxMessageBytes: 12582912 }); t.after(() => phone.close());
  { const big = new Promise(resolve => phone.addEventListener('message', ({ data: payload }) => resolve(payload), { once: true })); const closed = new Promise(resolve => phone.addEventListener('close', ev => resolve(ev), { once: true })); phone.send('BIG'); const got = await Promise.race([big, closed]); assert.equal(typeof got, 'string', 'large reply closed: ' + (got && got.code) + ' ' + (got && got.reason)); assert.equal(got.length, 9 * 1024 * 1024); }
  for (const [data, binary] of [['hello', false], [Buffer.from([0, 1, 255]), true]]) {
    const received = new Promise(resolve => phone.addEventListener('message', ({ data: payload }) => resolve(payload), { once: true }));
    phone.send(data); const result = await received; assert.equal(typeof result !== 'string', binary); assert.deepEqual(Buffer.from(result), Buffer.from(data));
  }
  wrongProtocol = true;
  const secondTicket = await fetch(origin + '/api/auth/ws-ticket', { method: 'POST', headers: { Authorization: `Bearer ${token}` } }); assert.equal(secondTicket.status, 200);
  const rejected = new WebSocket(dial, ['clover-relay-flow-v1', 'clover-gateway-v1', `clover-gateway-ticket.${(await secondTicket.json()).ticket}`], { handshakeTimeout: 3000 });
  const rejectedStatus = await new Promise((resolve, reject) => { rejected.once('unexpected-response', (_req, res) => { res.resume(); resolve(res.statusCode); }); rejected.once('open', () => reject(Error('wrong protocol upgraded'))); rejected.once('error', reject); });
  assert.notEqual(rejectedStatus, 101);
  wrongProtocol = false;
  for (const mode of ['failed', 'invalid', 'slow']) {
    upgradeMode = mode;
    const mint = await fetch(origin + '/api/auth/ws-ticket', { method: 'POST', headers: { Authorization: `Bearer ${token}` } });
    assert.equal(mint.status, 200);
    const candidate = new WebSocket(dial, ['clover-relay-flow-v1', 'clover-gateway-v1', `clover-gateway-ticket.${(await mint.json()).ticket}`], { handshakeTimeout: 3000 });
    const status = await new Promise((resolve, reject) => {
      candidate.once('unexpected-response', (_req, res) => { res.resume(); resolve(res.statusCode); });
      candidate.once('open', () => reject(Error(`${mode} backend upgraded`)));
      candidate.once('error', reject);
    });
    assert.notEqual(status, 101, mode);
  }
  // A revoked device must not receive a 101 while the real backend has
  // accepted TCP but not completed its WebSocket handshake.
  upgradeMode = 'slow';
  let receivedUpgrade;
  const upgradeArrived = new Promise(resolve => { receivedUpgrade = resolve; });
  backend.once('upgrade', () => receivedUpgrade());
  const mint = await fetch(origin + '/api/auth/ws-ticket', { method: 'POST', headers: { Authorization: `Bearer ${token}` } });
  assert.equal(mint.status, 200);
  const waiting = new WebSocket(dial, ['clover-relay-flow-v1', 'clover-gateway-v1', `clover-gateway-ticket.${(await mint.json()).ticket}`], { handshakeTimeout: 3000 });
  const result = new Promise((resolve, reject) => {
    waiting.once('unexpected-response', (_req, res) => { res.resume(); resolve(res.statusCode); });
    waiting.once('open', () => reject(Error('revoked pending handshake upgraded')));
    waiting.once('error', reject);
  });
  await upgradeArrived;
  await connector.replaceDevices([]);
  assert.notEqual(await result, 101);
});
