import test from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes, createHash } from 'node:crypto';
import { buildSync } from 'esbuild';
import { Miniflare } from 'miniflare';
import { rm } from 'node:fs/promises';
import { Installation } from '../src/worker.mjs';
import { FlowSocket } from '../src/flow-socket.mjs';

const origin = 'https://relay.test';
const waitFor = (ws, type) => new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(Error(`waiting for ${type}`)), 5000);
  ws.addEventListener(type, event => { clearTimeout(timer); resolve(event); }, { once: true });
});
const id = () => ({ id: randomBytes(16).toString('hex'), secret: randomBytes(32).toString('hex') });

test('ending a channel settles its connector backpressure waiter exactly once', () => {
  const install = new Installation({}, {}); let calls = 0;
  install.channels.set('channel', { epoch: 1, socket: null, phone: { close() {} }, waiters: new Set([() => { calls++; }]) });
  install.endChannel('channel'); install.endChannel('channel');
  assert.equal(calls, 1);
});

test('real Worker shares incoming connector reservations across installations and recovers', { timeout: 30000 }, async t => {
  buildSync({ entryPoints: ['src/worker.mjs'], outfile: 'dist/memory-worker.mjs', bundle: true, format: 'esm', platform: 'neutral' });
  t.after(() => rm('dist/memory-worker.mjs', { force: true }));
  const mf = new Miniflare({ workers: [{ name: 'relay', modules: true, scriptPath: 'dist/memory-worker.mjs', compatibilityDate: '2026-07-30', bindings: { ENROLLMENT_ENABLED: 'true' }, durableObjects: { INSTALLATIONS: { className: 'Installation', useSQLite: true }, DIRECTORY: { className: 'Directory', useSQLite: true }, GUARD: { className: 'Guard', useSQLite: true } } }] });
  t.after(() => mf.dispose());
  const identities = Array.from({ length: 3 }, id);
  for (const identity of identities) {
    const r = await mf.dispatchFetch(origin + '/v1/installations', { method: 'POST', body: JSON.stringify(identity) });
    assert.equal(r.status, 201);
  }
  async function connector(identity) {
    const r = await mf.dispatchFetch(`${origin}/v1/installations/${identity.id}/connect`, { headers: { Upgrade: 'websocket', Authorization: `Bearer ${identity.secret}`, 'Sec-WebSocket-Protocol': 'clover-relay-connector-v2' } });
    assert.equal(r.status, 101);
    const ws = r.webSocket;
    const hello = waitFor(ws, 'message'); ws.accept();
    const grant = JSON.parse((await hello).data).g;
    ws.send(JSON.stringify({ v: 1, t: 'hello', g: 'a'.repeat(32) }));
    return { ws, grant };
  }
  function declaration(conn) {
    conn.ws.send(JSON.stringify({ v: 1, t: 'data', g: conn.grant, n: 'b'.repeat(32), m: 'c'.repeat(32), i: 0, z: 12582912, b: false, f: false, d: Buffer.alloc(16384, 65).toString('base64') }));
  }
  const a = await connector(identities[0]), b = await connector(identities[1]), c = await connector(identities[2]);
  t.after(() => { for (const x of [a, b, c]) try { x.ws.close(); } catch {} });
  let ack = waitFor(a.ws, 'message'); declaration(a); assert.equal(JSON.parse((await ack).data).t, 'ack');
  ack = waitFor(b.ws, 'message'); declaration(b); assert.equal(JSON.parse((await ack).data).t, 'ack');
  const denied = waitFor(c.ws, 'close'); declaration(c); await denied;
  a.ws.close();
  // Wait for the first durable object to process the close before reconnecting.
  await new Promise(resolve => setTimeout(resolve, 100));
  const recovered = await connector(identities[2]); t.after(() => { try { recovered.ws.close(); } catch {} });
  ack = waitFor(recovered.ws, 'message'); declaration(recovered); assert.equal(JSON.parse((await ack).data).t, 'ack');
});

test('real Worker phone shares the connector ledger and recovers after overload', { timeout: 30000 }, async t => {
  buildSync({ entryPoints: ['src/worker.mjs'], outfile: 'dist/memory-phone-worker.mjs', bundle: true, format: 'esm', platform: 'neutral' });
  t.after(() => rm('dist/memory-phone-worker.mjs', { force: true }));
  const mf = new Miniflare({ workers: [{ name: 'relay', modules: true, scriptPath: 'dist/memory-phone-worker.mjs', compatibilityDate: '2026-07-30', bindings: { ENROLLMENT_ENABLED: 'true', HTTP_EXCHANGE_MB: '60' }, durableObjects: { INSTALLATIONS: { className: 'Installation', useSQLite: true }, DIRECTORY: { className: 'Directory', useSQLite: true }, GUARD: { className: 'Guard', useSQLite: true } } }] });
  t.after(() => mf.dispose());
  const identities = Array.from({ length: 4 }, id);
  for (const identity of identities) assert.equal((await mf.dispatchFetch(origin + '/v1/installations', { method: 'POST', body: JSON.stringify(identity) })).status, 201);
  const owner = identities[0], token = 'cdt_' + randomBytes(32).toString('hex');
  const connected = await mf.dispatchFetch(`${origin}/v1/installations/${owner.id}/connect`, { headers: { Upgrade: 'websocket', Authorization: `Bearer ${owner.secret}`, 'Sec-WebSocket-Protocol': 'clover-relay-connector-v2' } });
  connected.webSocket.accept(); const connector = new FlowSocket(connected.webSocket); t.after(() => connector.close());
  let controlResolve;
  const ticket = 'fixture-ticket-' + randomBytes(16).toString('hex');
  let active = connector;
  const handleMessage = ({ data }) => {
    const m = JSON.parse(data);
    if (m.type === 'control.result') controlResolve(m);
    if (m.type === 'http.request') {
      const body = m.target === '/api/auth/ws-ticket' ? { ticket, ttl_seconds: 30 } : { owner: owner.id };
      active.send(JSON.stringify({ type: 'http.response', id: m.id, status: 200, headers: { 'content-type': 'application/json' }, body: Buffer.from(JSON.stringify(body)).toString('base64') }));
    }
    if (m.type === 'ws.open') active.send(JSON.stringify({ type: 'ws.opened', id: m.id, protocol: 'clover-gateway-v1' }));
  };
  connector.addEventListener('message', handleMessage);
  const control = new Promise(resolve => { controlResolve = resolve; });
  connector.send(JSON.stringify({ type: 'devices.replace', id: 'control', devices: [{ hash: createHash('sha256').update(token).digest('hex'), expiresAt: Date.now() + 60000 }] }));
  assert.equal((await control).ok, true);
  const headers = { Authorization: `Bearer ${token}` };
  const before = await mf.dispatchFetch(origin + '/api/sessions', { headers }); assert.equal((await before.json()).owner, owner.id);
  const ticketResponse = await mf.dispatchFetch(origin + '/api/auth/ws-ticket', { method: 'POST', headers });
  assert.equal(ticketResponse.status, 200); await ticketResponse.arrayBuffer();
  const upgrade = await mf.dispatchFetch(origin + '/api/ws', { headers: { Upgrade: 'websocket', 'Sec-WebSocket-Protocol': `clover-relay-flow-v1, clover-gateway-v1, clover-gateway-ticket.${ticket}` } });
  assert.equal(upgrade.status, 101);
  const phoneRaw = upgrade.webSocket; const hello = waitFor(phoneRaw, 'message'); phoneRaw.accept();
  const grant = JSON.parse((await hello).data).g;
  phoneRaw.send(JSON.stringify({ v: 1, t: 'hello', g: 'a'.repeat(32) }));
  const hogs = [];
  t.after(() => { for (const h of hogs) try { h.close(); } catch {} try { phoneRaw.close(); } catch {} });
  for (let i = 1; i < 4; i++) {
    const identity = identities[i];
    const r = await mf.dispatchFetch(`${origin}/v1/installations/${identity.id}/connect`, { headers: { Upgrade: 'websocket', Authorization: `Bearer ${identity.secret}`, 'Sec-WebSocket-Protocol': 'clover-relay-connector-v2' } });
    const raw = r.webSocket; const first = waitFor(raw, 'message'); raw.accept();
    const g = JSON.parse((await first).data).g; hogs.push(raw);
    raw.send(JSON.stringify({ v: 1, t: 'hello', g: 'a'.repeat(32) }));
    const ack = waitFor(raw, 'message');
    raw.send(JSON.stringify({ v: 1, t: 'data', g, n: 'b'.repeat(32), m: 'c'.repeat(32), i: 0, z: i === 3 ? 8388608 : 12582912, b: false, f: false, d: Buffer.alloc(16384, 65).toString('base64') }));
    assert.equal(JSON.parse((await ack).data).t, 'ack');
  }
  const closed = waitFor(phoneRaw, 'close');
  phoneRaw.send(JSON.stringify({ v: 1, t: 'data', g: grant, n: 'b'.repeat(32), m: 'c'.repeat(32), i: 0, z: 1, b: true, f: true, d: 'eA==' }));
  await closed;
  hogs[0].close(); await new Promise(resolve => setTimeout(resolve, 100));
  const replacement = await mf.dispatchFetch(`${origin}/v1/installations/${owner.id}/connect`, { headers: { Upgrade: 'websocket', Authorization: `Bearer ${owner.secret}`, 'Sec-WebSocket-Protocol': 'clover-relay-connector-v2' } });
  assert.equal(replacement.status, 101); replacement.webSocket.accept();
  active = new FlowSocket(replacement.webSocket); active.addEventListener('message', handleMessage); t.after(() => active.close());
  const overloaded = await mf.dispatchFetch(origin + '/api/sessions', { headers });
  assert.equal(overloaded.status, 503, 'remaining transport reservations must compete with HTTP');
  for (const hog of hogs.slice(1)) hog.close();
  await new Promise(resolve => setTimeout(resolve, 100));
  const after = await mf.dispatchFetch(origin + '/api/sessions', { headers });
  assert.equal(after.status, 200); assert.equal((await after.json()).owner, owner.id);
});
