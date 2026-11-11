import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:http';
import { createHash, randomBytes } from 'node:crypto';
import { buildSync } from 'esbuild';
import { Miniflare } from 'miniflare';
import WebSocket, { WebSocketServer } from 'ws';
import { ensureEnrolled, openConnector } from '../src/desktop-connector.mjs';
import { allowMobileRequest } from '../src/admission.mjs';

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function rig(t, { backendUpgrade } = {}) {
  buildSync({ entryPoints: ['src/worker.mjs'], outfile: 'dist/worker.mjs', bundle: true, format: 'esm', platform: 'neutral' });
  const mf = new Miniflare({ workers: [{ name: 'relay', modules: true, scriptPath: 'dist/worker.mjs', compatibilityDate: '2026-07-30', bindings: { ENROLLMENT_ENABLED: 'true', REQUEST_TIMEOUT_MS: '1000', ALLOW_TEST_TIMEOUT: 'true', ALLOW_LEGACY_TEST_TRANSPORT: 'true' }, durableObjects: { INSTALLATIONS: { className: 'Installation', useSQLite: true }, DIRECTORY: { className: 'Directory', useSQLite: true }, GUARD: { className: 'Guard', useSQLite: true } } }] });
  t.after(() => mf.dispose()); const origin = (await mf.ready).origin;
  const dir = await mkdtemp(join(tmpdir(), 'relay-desk-')); t.after(() => rm(dir, { recursive: true, force: true }));
  const { identity } = await ensureEnrolled(origin, join(dir, 'identity'), { allowInsecureLocalhost: true });
  const token = 'cdt_' + randomBytes(32).toString('hex');
  const seen = { urls: [], clientMessages: [], closed: 0 };
  const backend = createServer((req, res) => {
    const ticket = randomBytes(32).toString('base64url');
    res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ ticket, ttl_seconds: 30 }));
  });
  const wss = new WebSocketServer({ noServer: true, handleProtocols: p => p.has('clover-gateway-v1') ? 'clover-gateway-v1' : false });
  backend.on('upgrade', (req, socket, head) => {
    seen.urls.push(req.url);
    if (req.url !== '/desk/stream?v=1' && req.url !== '/api/ws') return socket.destroy();
    wss.handleUpgrade(req, socket, head, ws => { ws.deskPath = req.url; wss.emit('connection', ws); });
  });
  wss.on('connection', ws => {
    if (backendUpgrade) backendUpgrade(ws, seen);
    ws.on('message', (data, binary) => { seen.clientMessages.push(binary ? 'bin' : String(data)); });
    ws.on('close', () => { seen.closed++; });
  });
  await new Promise(r => backend.listen(0, '127.0.0.1', r)); t.after(() => new Promise(r => { for (const ws of wss.clients) ws.terminate(); backend.close(r); }));
  const connector = await openConnector({ relayOrigin: origin, identity, backendOrigin: `http://127.0.0.1:${backend.address().port}`, allowInsecureLocalhost: true }); t.after(() => connector.close());
  await connector.replaceDevices([{ hash: createHash('sha256').update(token).digest('hex'), expiresAt: Date.now() + 60000 }]);
  const dial = async (path = '/desk/stream') => {
    const minted = await fetch(origin + '/api/auth/ws-ticket', { method: 'POST', headers: { Authorization: `Bearer ${token}` } }); assert.equal(minted.status, 200);
    const u = new URL(path, origin); u.protocol = 'ws:';
    const ws = new WebSocket(u, ['clover-gateway-v1', `clover-gateway-ticket.${(await minted.json()).ticket}`], { handshakeTimeout: 3000, maxPayload: 4 * 1024 * 1024 });
    t.after(() => ws.terminate());
    return ws;
  };
  return { origin, dial, seen, wss };
}
const opened = ws => new Promise((res, rej) => { ws.once('open', res); ws.once('error', rej); ws.once('unexpected-response', (_q, r) => rej(Error('status ' + r.statusCode))); });
const closed = ws => new Promise(res => ws.once('close', (code, reason) => res({ code, reason: String(reason) })));

test('admission: stream is WebSocket-only, never reachable as an HTTP request', () => {
  for (const m of ['GET', 'POST']) assert.equal(allowMobileRequest(m, '/desk/stream'), false);
  assert.equal(allowMobileRequest('GET', '/desk/stream?v=1'), false);
});

test('phone <-> gateway stream passes binary frames up to 900 KB intact and text acks back', { timeout: 20000 }, async t => {
  const frame = Buffer.concat([Buffer.from([0, 0, 0, 2, 0x7b, 0x7d]), randomBytes(900_000 - 6)]);
  const { dial, seen } = await rig(t, { backendUpgrade: ws => { if (ws.deskPath.startsWith('/desk/stream')) { ws.send(JSON.stringify({ t: 'hello', v: 1 })); ws.send(frame, { binary: true }); } } });
  const ws = await dial(); const got = []; ws.on('message', (d, b) => got.push({ b, d }));
  await opened(ws);
  for (let i = 0; i < 50 && got.length < 2; i++) await sleep(100);
  assert.equal(got[0].b, false); assert.equal(JSON.parse(String(got[0].d)).t, 'hello');
  assert.equal(got[1].b, true); assert.ok(Buffer.from(got[1].d).equals(frame));
  ws.send(JSON.stringify({ t: 'ack', seq: 1 })); await sleep(300);
  assert.deepEqual(seen.clientMessages, ['{"t":"ack","seq":1}']);
  assert.deepEqual(seen.urls, ['/desk/stream?v=1']);
});

test('stream limits end only the stream: oversize/binary up, second stream per device, then chat still works', { timeout: 30000 }, async t => {
  const { dial, seen } = await rig(t);
  const a = await dial(); await opened(a);
  const second = await dial();
  const status = await new Promise((res, rej) => { second.once('unexpected-response', (_q, r) => { r.resume(); res(r.statusCode); }); second.once('open', () => rej(Error('second stream opened'))); second.once('error', () => res('error')); });
  assert.notEqual(status, 101);
  const c = closed(a); a.send('x'.repeat(5000));
  const end = await c; assert.equal(end.code, 1008);
  for (let i = 0; i < 30 && !seen.closed; i++) await sleep(100);
  assert.ok(seen.closed >= 1, 'gateway side socket was closed too');
  const b = await dial(); await opened(b); const cb = closed(b); b.send(Buffer.from([1, 2, 3])); assert.equal((await cb).code, 1008);
  const chat = await dial('/api/ws'); await opened(chat); chat.send('hi'); await sleep(300);
  assert.ok(seen.clientMessages.includes('hi'), 'chat path unaffected');
  assert.ok(seen.urls.includes('/api/ws'));
});

test('stream closes cleanly when the gateway drops, and when the phone drops', { timeout: 20000 }, async t => {
  const { dial, seen, wss } = await rig(t);
  const a = await dial(); await opened(a); await sleep(200);
  const ca = closed(a); for (const s of wss.clients) s.close(1001, 'bye');
  assert.ok((await ca).code >= 1000);
  const b = await dial(); await opened(b); await sleep(200);
  const before = seen.closed; b.close(1000);
  for (let i = 0; i < 30 && seen.closed === before; i++) await sleep(100);
  assert.ok(seen.closed > before, 'gateway socket closed after phone left');
  const c = await dial(); await opened(c); c.terminate();
});

test('per-stream byte rate: a flooding gateway is either throttled by flow control or ended with 1008, never unbounded', { timeout: 30000 }, async t => {
  const chunk = randomBytes(900_000);
  const { dial } = await rig(t, { backendUpgrade: ws => { if (ws.deskPath.startsWith('/desk/stream')) { let n = 0; const iv = setInterval(() => { if (ws.readyState !== 1) return clearInterval(iv); ws.send(chunk, { binary: true }); if (++n > 30) clearInterval(iv); }, 5); ws.on('close', () => clearInterval(iv)); } } });
  const ws = await dial(); let bytes = 0, first = 0, worst = 0, win = Date.now(), inWin = 0;
  ws.on('message', d => { first ||= Date.now(); bytes += d.length; const now = Date.now(); if (now - win >= 1000) { win = now; inWin = 0; } inWin += d.length; worst = Math.max(worst, inWin); });
  await opened(ws);
  const end = await Promise.race([closed(ws), sleep(8000).then(() => null)]);
  if (end) assert.equal(end.code, 1008); else assert.ok(worst <= 7 * 1024 * 1024, 'worst 1s window ' + worst);
  assert.ok(bytes > 0);
});
