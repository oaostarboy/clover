import test from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes, createHash } from 'node:crypto';
import { buildSync } from 'esbuild';
import { Miniflare } from 'miniflare';
import { rm } from 'node:fs/promises';
import { FlowSocket } from '../src/flow-socket.mjs';

const origin = 'https://relay.test';
const push = async (ws, d) => { while (ws.queue.length >= ws.queueCount) await new Promise(r => { const f = () => { ws.removeEventListener('drain', f); r(); }; ws.addEventListener('drain', f); setTimeout(f, 20); }); ws.send(d); };
async function setup(t, stall = false, logs = null) {
  buildSync({ entryPoints: ['src/worker.mjs'], outfile: 'dist/burst-worker.mjs', bundle: true, format: 'esm', platform: 'neutral' });
  t.after(() => rm('dist/burst-worker.mjs', { force: true }));
  const mf = new Miniflare({ ...(logs ? { handleStructuredLogs: l => { logs.push(String(l.message) + '\n'); } } : {}), workers: [{ name: 'relay', modules: true, scriptPath: 'dist/burst-worker.mjs', compatibilityDate: '2026-07-30',  bindings: { ENROLLMENT_ENABLED: 'true' }, durableObjects: { INSTALLATIONS: { className: 'Installation', useSQLite: true }, DIRECTORY: { className: 'Directory', useSQLite: true }, GUARD: { className: 'Guard', useSQLite: true } } }] });
  t.after(() => mf.dispose());
  const owner = { id: randomBytes(16).toString('hex'), secret: randomBytes(32).toString('hex') };
  assert.equal((await mf.dispatchFetch(origin + '/v1/installations', { method: 'POST', body: JSON.stringify(owner) })).status, 201);
  const token = 'cdt_' + randomBytes(32).toString('hex');
  const r = await mf.dispatchFetch(`${origin}/v1/installations/${owner.id}/connect`, { headers: { Upgrade: 'websocket', Authorization: `Bearer ${owner.secret}`, 'Sec-WebSocket-Protocol': 'clover-relay-connector-v2' } });
  assert.equal(r.status, 101); r.webSocket.accept();
  const connector = new FlowSocket(r.webSocket); t.after(() => connector.close());
  const seen = { data: new Map(), open: 0, methods: [] }; let ctl; const control = new Promise(res => { ctl = res; });
  const ticket = () => 'fixture-ticket-' + randomBytes(16).toString('hex'); let nextTicket;
  const gate = { wait: Promise.resolve(), release() {}, hold() { this.wait = new Promise(r => { this.release = r; }); } }; if (stall) gate.hold();
  connector.addEventListener('message', async ({ data }) => {
    const m = JSON.parse(data);
    if (m.type === 'control.result') ctl(m);
    if (m.type === 'http.request') connector.send(JSON.stringify({ type: 'http.response', id: m.id, status: 200, headers: { 'content-type': 'application/json' }, body: Buffer.from(JSON.stringify({ ticket: nextTicket, ttl_seconds: 30 })).toString('base64') }));
    if (m.type === 'ws.open') { seen.open++; connector.send(JSON.stringify({ type: 'ws.opened', id: m.id, protocol: 'clover-gateway-v1' })); }
    if (m.type === 'ws.data') { seen.data.set(m.id, (seen.data.get(m.id) || 0) + 1); try { const j = JSON.parse(m.data); seen.methods.push(j.method); if (j.method === 'push.register') (j.params.token === 'dd'.repeat(32) ? (f => setTimeout(f, 700)) : (f => f()))(() => connector.send(JSON.stringify({ type: 'ws.data', id: m.id, binary: false, data: JSON.stringify(j.params.token === 'ee'.repeat(32) ? { jsonrpc: '2.0', id: j.id, error: { code: 4030, message: 'operation is not permitted for this device' } } : { jsonrpc: '2.0', id: j.id, result: { registered: true, enabled: true } }) }))); } catch {} await gate.wait; }
  });
  connector.send(JSON.stringify({ type: 'devices.replace', id: 'control', devices: [{ hash: createHash('sha256').update(token).digest('hex'), expiresAt: Date.now() + 60000 }] }));
  assert.equal((await control).ok, true);
  async function phone() {
    nextTicket = ticket();
    const tr = await mf.dispatchFetch(origin + '/api/auth/ws-ticket', { method: 'POST', headers: { Authorization: `Bearer ${token}` } });
    const tb = await tr.text(); assert.equal(tr.status, 200, tb);
    const up = await mf.dispatchFetch(origin + '/api/ws', { headers: { Upgrade: 'websocket', 'Sec-WebSocket-Protocol': `clover-relay-flow-v1, clover-gateway-v1, clover-gateway-ticket.${nextTicket}` } });
    assert.equal(up.status, 101); up.webSocket.accept(); return new FlowSocket(up.webSocket);
  }
  return { phone, seen, connector, gate };
}

test('phone burst of 1000 small messages is all forwarded without closing', { timeout: 60000 }, async t => {
  const { phone, seen, gate } = await setup(t, true);
  const ws = await phone(); let closed = null; ws.addEventListener('close', e => { closed = e.reason || e.code; });
  for (let i = 0; i < 1000 && !closed; i++) await push(ws, JSON.stringify({ i }));
  gate.release();
  const deadline = Date.now() + 40000;
  const total = () => [...seen.data.values()].reduce((a, b) => a + b, 0);
  while (total() < 1000 && !closed && Date.now() < deadline) await new Promise(r => setTimeout(r, 50));
  assert.equal(closed, null);
  assert.equal(total(), 1000);
  ws.close();
});

test('channels ended while the connector link is full do not exhaust link listeners', { timeout: 60000 }, async t => {
  const { phone, seen, gate } = await setup(t, true);
  // Stall the connector: stop granting credit so the relay-side queue fills.
  for (let wave = 0; wave < 6; wave++) {
    gate.release(); await new Promise(r => setTimeout(r, 300));
    const phones = []; for (let n = 0; n < 4; n++) phones.push(await phone());
    gate.hold();
    for (const ws of phones) for (let i = 0; i < 12; i++) ws.send(JSON.stringify({ wave, i }));
    await new Promise(r => setTimeout(r, 50));
    for (const ws of phones) ws.close();
    await new Promise(r => setTimeout(r, 50));
  }
  gate.release();
  await new Promise(r => setTimeout(r, 300));
  const ws = await phone(); let closed = null; ws.addEventListener('close', e => { closed = e.reason || e.code; });
  await push(ws, 'final'); await new Promise(r => setTimeout(r, 1500));
  assert.equal(closed, null);
  assert.ok(seen.data.size >= 1);
});

test('identical repeated push.register is answered locally after the first succeeds; a different token still goes upstream', { timeout: 30000 }, async t => {
  const { phone, seen } = await setup(t);
  const ws = await phone(); const replies = new Map(); ws.addEventListener('message', e => { try { const j = JSON.parse(e.data); replies.set(j.id, j); } catch {} });
  const reg = (id, token) => push(ws, JSON.stringify({ jsonrpc: '2.0', id, method: 'push.register', params: { token, env: 'production', device_name: 'iPhone' } }));
  await reg('a1', 'ab'.repeat(32)); await new Promise(r => setTimeout(r, 500));
  for (let i = 0; i < 50; i++) await reg('r' + i, 'ab'.repeat(32));
  await new Promise(r => setTimeout(r, 800));
  assert.equal(seen.methods.filter(m => m === 'push.register').length, 1);
  for (let i = 0; i < 50; i++) assert.deepEqual(replies.get('r' + i)?.result, { registered: true, enabled: true });
  await reg('b1', 'cd'.repeat(32)); await new Promise(r => setTimeout(r, 500));
  assert.equal(seen.methods.filter(m => m === 'push.register').length, 2);
  ws.close();
});

test('identical repeated push.register that failed with a device-permission error is answered locally with the same error', { timeout: 30000 }, async t => {
  const { phone, seen } = await setup(t);
  const ws = await phone(); const replies = new Map(); ws.addEventListener('message', e => { try { const j = JSON.parse(e.data); replies.set(j.id, j); } catch {} });
  const reg = (id, token) => push(ws, JSON.stringify({ jsonrpc: '2.0', id, method: 'push.register', params: { token, env: 'production', device_name: 'iPhone' } }));
  await reg('a1', 'ee'.repeat(32)); await new Promise(r => setTimeout(r, 500));
  for (let i = 0; i < 50; i++) await reg('r' + i, 'ee'.repeat(32));
  await new Promise(r => setTimeout(r, 800));
  assert.equal(seen.methods.filter(m => m === 'push.register').length, 1);
  for (let i = 0; i < 50; i++) assert.equal(replies.get('r' + i)?.error?.code, 4030);
  ws.close();
});

test('300 identical push.register sent before any reply are coalesced to one upstream call; all get replies; channel stays open', { timeout: 30000 }, async t => {
  const { phone, seen } = await setup(t);
  const ws = await phone(); let closed = null; ws.addEventListener('close', e => { closed = e.reason || e.code; });
  const replies = new Map(); ws.addEventListener('message', e => { try { const j = JSON.parse(e.data); replies.set(j.id, j); } catch {} });
  for (let i = 0; i < 300; i++) await push(ws, JSON.stringify({ jsonrpc: '2.0', id: 'r' + i, method: 'push.register', params: { token: 'dd'.repeat(32), env: 'production', device_name: 'iPhone' } }));
  await push(ws, JSON.stringify({ jsonrpc: '2.0', id: 's1', method: 'session.create', params: {} }));
  const deadline = Date.now() + 15000;
  while (replies.size < 300 && !closed && Date.now() < deadline) await new Promise(r => setTimeout(r, 50));
  assert.equal(closed, null);
  assert.equal(seen.methods.filter(m => m === 'push.register').length, 1);
  assert.equal(seen.methods.filter(m => m === 'session.create').length, 1);
  for (let i = 0; i < 300; i++) assert.deepEqual(replies.get('r' + i)?.result, { registered: true, enabled: true }, 'r' + i);
  ws.close();
});

test('relay.ping is answered locally and never reaches the connector, even while the connector is stalled', { timeout: 30000 }, async t => {
  const { phone, seen } = await setup(t, true);
  const ws = await phone(); let closed = null; ws.addEventListener('close', e => { closed = e.reason || e.code; });
  const replies = new Map(); ws.addEventListener('message', e => { try { const j = JSON.parse(e.data); replies.set(j.id, j); } catch {} });
  const t0 = Date.now();
  for (let i = 0; i < 3; i++) await push(ws, JSON.stringify({ jsonrpc: '2.0', id: 'p' + i, method: 'relay.ping', params: {} }));
  while (replies.size < 3 && Date.now() - t0 < 3000) await new Promise(r => setTimeout(r, 20));
  assert.equal(closed, null);
  for (let i = 0; i < 3; i++) { const r = replies.get('p' + i); assert.equal(r?.jsonrpc, '2.0'); assert.equal(r.result.ok, true); assert.ok(Math.abs(r.result.t - Date.now()) < 5000); }
  await new Promise(r => setTimeout(r, 300));
  assert.equal(seen.methods.filter(m => m === 'relay.ping').length, 0);
  assert.equal([...seen.data.values()].reduce((a, b) => a + b, 0), 0);
  ws.close();
});

test('relay.diag is not forwarded, gets no reply, is validated and logged per close entry', { timeout: 30000 }, async t => {
  const logs = []; const { phone, seen } = await setup(t, false, logs);
  const ws = await phone(); let closed = null; ws.addEventListener('close', e => { closed = e.reason || e.code; });
  const got = []; ws.addEventListener('message', e => got.push(e.data));
  const close = (i, extra = {}) => ({ at: 1000 + i, code: 1006, reason: 'x'.repeat(130), clean: false, by: 'native', appState: 'active', openMs: 5, net: null, secret: 'nope', ...extra });
  const diag = closes => JSON.stringify({ jsonrpc: '2.0', method: 'relay.diag', params: { build: '55', closes } });
  await push(ws, diag(Array.from({ length: 30 }, (_, i) => close(i))));
  await push(ws, diag([close(99, { code: 'bad', by: 'evil' }), 'junk']));
  await push(ws, JSON.stringify({ jsonrpc: '2.0', method: 'relay.diag', params: { build: '55', closes: [close(0)], pad: 'y'.repeat(9000) } }));
  await push(ws, 'not json relay.diag');
  await push(ws, JSON.stringify({ jsonrpc: '2.0', id: 'z', method: 'session.create', params: {} }));
  await new Promise(r => setTimeout(r, 700));
  const evs = logs.join('').split('\n').map(l => { try { return JSON.parse(l); } catch { return null; } }).filter(e => e?.event === 'phone_diag');
  assert.equal(evs.length, 21);
  assert.equal(evs[0].reason.length, 120); assert.equal(evs[0].secret, undefined); assert.equal(evs[0].code, 1006); assert.equal(evs[0].net, null); assert.equal(evs[0].by, 'native');
  assert.ok(evs[0].ch && evs[0].dev); assert.equal(evs[20].code, undefined); assert.equal(evs[20].by, undefined);
  assert.equal(seen.methods.filter(m => m === 'relay.diag').length, 0);
  assert.equal(got.filter(d => !String(d).includes('"id":"z"')).length, 0, 'no reply to diag');
  ws.close();
});
