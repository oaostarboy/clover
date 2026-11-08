import test from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes, createHash } from 'node:crypto';
import { buildSync } from 'esbuild';
import { Miniflare } from 'miniflare';
import { rm } from 'node:fs/promises';
import { FlowSocket } from '../src/flow-socket.mjs';

const origin = 'https://relay.test';
const push = async (ws, d) => { while (ws.queue.length >= ws.queueCount) await new Promise(r => { const f = () => { ws.removeEventListener('drain', f); r(); }; ws.addEventListener('drain', f); setTimeout(f, 20); }); ws.send(d); };
async function setup(t, stall = false) {
  buildSync({ entryPoints: ['src/worker.mjs'], outfile: 'dist/burst-worker.mjs', bundle: true, format: 'esm', platform: 'neutral' });
  t.after(() => rm('dist/burst-worker.mjs', { force: true }));
  const mf = new Miniflare({ workers: [{ name: 'relay', modules: true, scriptPath: 'dist/burst-worker.mjs', compatibilityDate: '2026-07-30', bindings: { ENROLLMENT_ENABLED: 'true' }, durableObjects: { INSTALLATIONS: { className: 'Installation', useSQLite: true }, DIRECTORY: { className: 'Directory', useSQLite: true }, GUARD: { className: 'Guard', useSQLite: true } } }] });
  t.after(() => mf.dispose());
  const owner = { id: randomBytes(16).toString('hex'), secret: randomBytes(32).toString('hex') };
  assert.equal((await mf.dispatchFetch(origin + '/v1/installations', { method: 'POST', body: JSON.stringify(owner) })).status, 201);
  const token = 'cdt_' + randomBytes(32).toString('hex');
  const r = await mf.dispatchFetch(`${origin}/v1/installations/${owner.id}/connect`, { headers: { Upgrade: 'websocket', Authorization: `Bearer ${owner.secret}`, 'Sec-WebSocket-Protocol': 'clover-relay-connector-v2' } });
  assert.equal(r.status, 101); r.webSocket.accept();
  const connector = new FlowSocket(r.webSocket); t.after(() => connector.close());
  const seen = { data: new Map(), open: 0 }; let ctl; const control = new Promise(res => { ctl = res; });
  const ticket = () => 'fixture-ticket-' + randomBytes(16).toString('hex'); let nextTicket;
  const gate = { wait: Promise.resolve(), release() {}, hold() { this.wait = new Promise(r => { this.release = r; }); } }; if (stall) gate.hold();
  connector.addEventListener('message', async ({ data }) => {
    const m = JSON.parse(data);
    if (m.type === 'control.result') ctl(m);
    if (m.type === 'http.request') connector.send(JSON.stringify({ type: 'http.response', id: m.id, status: 200, headers: { 'content-type': 'application/json' }, body: Buffer.from(JSON.stringify({ ticket: nextTicket, ttl_seconds: 30 })).toString('base64') }));
    if (m.type === 'ws.open') { seen.open++; connector.send(JSON.stringify({ type: 'ws.opened', id: m.id, protocol: 'clover-gateway-v1' })); }
    if (m.type === 'ws.data') { seen.data.set(m.id, (seen.data.get(m.id) || 0) + 1); await gate.wait; }
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
