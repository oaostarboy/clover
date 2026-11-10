import test from 'node:test';
import assert from 'node:assert/strict';
import { createDesktopPairing } from '../src/desktop-pairing.mjs';

const conn = { mode: 'local', baseUrl: 'http://127.0.0.1:12345' };
const request = async (_c, path) => path === '/api/mobile/pairing' ? { configured: false }
  : { profile: 'default', gatewayId: 'a'.repeat(64), devices: [] };
const wait = ms => new Promise(r => setTimeout(r, ms));
const until = async (fn, ms = 3000) => { const end = Date.now() + ms; while (!fn()) { if (Date.now() > end) throw Error('timeout'); await wait(5); } };

function harness(connectImpl, extra = {}) {
  const h = { opens: 0, closes: 0, errors: [] };
  const service = createDesktopPairing({ identityPath: () => '/tmp/x', request, enroll: async () => ({ identity: {} }),
    syncIntervalMs: 20, backoffMinMs: 10, backoffMaxMs: 40, random: () => 1,
    onSyncError: (e, n, fatal) => h.errors.push([String(e.message), n, fatal]),
    connect: async () => { h.opens++; return connectImpl(h); }, ...extra });
  h.service = service; return h;
}

test('connector that keeps dying is reopened forever (no give-up after 6 failures)', async t => {
  let live = null;
  const h = harness(hh => { const c = { alive: true, replaceDevices: async () => { if (!c.alive) throw Error('socket closed'); }, close() { hh.closes++; c.alive = false; } }; live = c; return c; });
  t.after(() => h.service.close());
  await h.service.status('s', 'default', conn);
  for (let i = 1; i <= 10; i++) { live.alive = false; await until(() => h.opens > i); }
  assert.ok(h.opens >= 11);
});

test('transient relay failures (5xx / network / 1012) back off, never stop, and recover', async t => {
  let failing = 0;
  const h = harness(() => ({ replaceDevices: async () => {}, close() {} }), {
    connect: async () => { h.opens++; if (failing > 0) { failing--; throw Error('Unexpected server response: 503'); } return { replaceDevices: async () => {}, close() { h.closes++; } }; } });
  t.after(() => h.service.close());
  await h.service.status('s', 'default', conn);
  h.service.reconnect(); failing = 12;
  await until(() => h.errors.length >= 12 && h.opens >= 14, 5000);
  assert.ok(h.errors.every(e => e[2] === false));
  assert.deepEqual(h.errors.slice(0, 3).map(e => e[1]), [1, 2, 3]);
});

test('backoff is jittered between 50% and 100% and capped at 30 s', async t => {
  const delays = [];
  const svc = createDesktopPairing({ identityPath: () => '/x', request: async () => { throw Error('ECONNREFUSED'); }, random: () => 0, syncIntervalMs: 0,
    sleep: async ms => { delays.push(ms); if (delays.length >= 9) svc.close(); } });
  await svc.keepAlive('s', 'default', () => conn);
  assert.deepEqual(delays.slice(0, 6), [500, 1000, 2000, 4000, 8000, 15000]);
  assert.ok(delays.every(d => d <= 30000));
});

test('keepAlive retries transient launch errors until the link is up', async t => {
  let n = 0;
  const svc = createDesktopPairing({ identityPath: () => '/x', syncIntervalMs: 0, sleep: async () => {}, enroll: async () => ({ identity: {} }),
    connect: async () => { if (++n < 5) throw Error('network down'); return { replaceDevices: async () => {}, close() {} }; }, request });
  t.after(() => svc.close());
  const status = await svc.keepAlive('s', 'default', () => conn);
  assert.equal(status.configured, true); assert.equal(n, 5);
});

test('failure counter resets only after the relay acks the device list', async t => {
  let acked = false;
  const h = harness(() => ({ replaceDevices: async () => { if (!acked) throw Error('no ack'); }, close() {} }));
  t.after(() => h.service.close());
  await assert.rejects(h.service.status('s', 'default', conn), /no ack/);
  acked = true;
  const status = await h.service.status('s', 'default', conn);
  assert.equal(status.configured, true);
});

test('auth rejection by the relay stops retrying and is shown in pairing status', async t => {
  const h = harness(() => ({ replaceDevices: async () => {}, close() {} }), {
    connect: async () => { h.opens++; throw Error('Unexpected server response: 401'); } });
  t.after(() => h.service.close());
  const status = await h.service.keepAlive('s', 'default', () => conn);
  assert.equal(status.reason, 'relay_auth_rejected'); assert.equal(status.configured, false);
  const opens = h.opens; await wait(120);
  assert.equal(h.opens, opens); // no more attempts
  assert.equal((await h.service.status('s', 'default', conn)).reason, 'relay_auth_rejected');
});

test('resume/network-change reconnect() reopens immediately and clears a rejection', async t => {
  let reject = true;
  const h = harness(() => ({ replaceDevices: async () => {}, close() { h.closes++; } }), {
    connect: async () => { h.opens++; if (reject) throw Error('enrollment failed: 403'); return { replaceDevices: async () => {}, close() { h.closes++; } }; } });
  t.after(() => h.service.close());
  assert.equal((await h.service.keepAlive('s', 'default', () => conn)).reason, 'relay_auth_rejected');
  reject = false; h.service.reconnect();
  assert.equal((await h.service.status('s', 'default', conn)).configured, true);
  const before = h.opens; h.service.reconnect();
  await until(() => h.opens > before);
});
