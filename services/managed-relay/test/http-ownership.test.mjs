import test from 'node:test';
import assert from 'node:assert/strict';
import { Installation } from '../src/worker.mjs';
import { MemoryLedger } from '../src/memory-ledger.mjs';

const deviceHash = 'a'.repeat(64);
const identity = { id: 'owner', devices: [{ hash: deviceHash, expiresAt: Date.now() + 60000 }] };
const request = () => new Request('https://internal/forward', { method: 'POST', headers: { 'x-relay-device-hash': deviceHash, 'x-relay-method': 'GET', 'x-relay-target': '/api/sessions' } });

test('storage failure after HTTP admission releases ownership', async () => {
  let reads = 0;
  const instance = new Installation({ storage: { async get() { if (++reads === 2) throw Error('storage unavailable'); return identity; } } }, { ALLOW_TEST_TIMEOUT: 'true', REQUEST_TIMEOUT_MS: '100' });
  instance.socket = { send() {} };
  await assert.rejects(instance.fetch(request()), /storage unavailable/);
  assert.equal(instance.httpBusy, false);
  assert.equal((await instance.fetch(request())).status, 504);
});

test('backend error body retains shared HTTP lease until cancellation', async () => {
  const create = () => {
    const instance = new Installation({ storage: { async get() { return identity; } } }, { ALLOW_TEST_TIMEOUT: 'true', REQUEST_TIMEOUT_MS: '1000' });
    instance.socket = { send() {} }; return instance;
  };
  const first = create(), second = create();
  const pending = first.fetch(request());
  while (!first.pending.size) await new Promise(resolve => setImmediate(resolve));
  [...first.pending.values()][0](new Response(new Uint8Array(1024), { status: 500 }), true);
  const response = await pending;
  assert.equal(response.status, 500);
  assert.equal((await first.fetch(request())).status, 503);
  assert.equal((await second.fetch(request())).status, 503);
  await response.body.cancel();
  const recovered = second.fetch(request());
  while (!second.pending.size) await new Promise(resolve => setImmediate(resolve));
  [...second.pending.values()][0](new Response('ok'), true);
  assert.equal(await (await recovered).text(), 'ok');
  assert.equal(second.httpBusy, false);
});

test('request timeout retains HTTP ownership until late response processing unwinds', async () => {
  const instance = new Installation({ storage: { async get() { return identity; } } }, { ALLOW_TEST_TIMEOUT: 'true', REQUEST_TIMEOUT_MS: '100' });
  instance.socket = { send() {} };
  const pending = instance.fetch(request());
  while (!instance.pending.size) await new Promise(resolve => setImmediate(resolve));
  const finish = [...instance.pending.values()][0];
  finish.processing = true;
  assert.equal((await pending).status, 504);
  assert.equal(instance.httpBusy, true);
  await finish(new Response('late'), true);
  finish.processingDone();
  assert.equal(instance.httpBusy, false);
});

test('shared live ledger records combined HTTP and synthetic transport weights', async t => {
  const original = MemoryLedger.prototype.reserve; let ledger, highWater = 0;
  MemoryLedger.prototype.reserve = function (weight) {
    const release = original.call(this, weight);
    if (release) { ledger = this; highWater = Math.max(highWater, this.used); }
    return release;
  };
  t.after(() => { MemoryLedger.prototype.reserve = original; });
  const instance = new Installation({ storage: { async get() { return identity; } } }, { ALLOW_TEST_TIMEOUT: 'true', REQUEST_TIMEOUT_MS: '1000' });
  instance.socket = { send() {} };
  const pending = instance.fetch(request());
  while (!instance.pending.size) await new Promise(resolve => setImmediate(resolve));
  const releaseTransport = ledger.reserve(33.5 * 1024 * 1024);
  assert.ok(releaseTransport);
  assert.equal(highWater, 93.5 * 1024 * 1024);
  assert.ok(highWater < ledger.limit);
  releaseTransport(); [...instance.pending.values()][0](new Response('ok'), true);
  await (await pending).text(); assert.equal(ledger.used, 0);
  t.diagnostic(`weighted high water ${highWater} bytes; synthetic transport reservation`);
});

test('stalled upload is cancelled at body deadline and releases admission', async () => {
  let cancelled = false;
  const body = new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array([1])); }, cancel() { cancelled = true; } });
  const instance = new Installation({ storage: { async get() { return identity; } } }, { ALLOW_TEST_TIMEOUT: 'true', BODY_TIMEOUT_MS: '100' });
  instance.socket = { send() { throw Error('must not reach backend'); } };
  const upload = new Request('https://internal/forward', { method: 'POST', headers: { 'x-relay-device-hash': deviceHash, 'x-relay-method': 'POST', 'x-relay-target': '/api/audio/transcribe' }, body, duplex: 'half' });
  assert.equal((await instance.fetch(upload)).status, 408);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(cancelled, true); assert.equal(instance.httpBusy, false);
});

test('connector replacement fences a body that finishes after replacement', async () => {
  let unblock, actions = 0;
  const held = new Promise(resolve => { unblock = resolve; });
  const body = new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array([1])); }, async pull(controller) { await held; controller.close(); } });
  const instance = new Installation({ storage: { async get() { return identity; } } }, { ALLOW_TEST_TIMEOUT: 'true', BODY_TIMEOUT_MS: '1000' });
  instance.socket = { send() { actions++; } };
  const upload = new Request('https://internal/forward', { method: 'POST', headers: { 'x-relay-device-hash': deviceHash, 'x-relay-method': 'POST', 'x-relay-target': '/api/audio/transcribe' }, body, duplex: 'half' });
  const pending = instance.fetch(upload);
  while (!instance.httpBusy) await new Promise(resolve => setImmediate(resolve));
  instance.epoch++; instance.socket = { send() { actions++; } }; unblock();
  assert.equal((await pending).status, 503); assert.equal(actions, 0); assert.equal(instance.httpBusy, false);
});

test('body deadline keeps its lease until asynchronous reader cancellation completes', async () => {
  let finishCancel, cancelStarted;
  const cancellation = new Promise(resolve => { finishCancel = resolve; });
  const started = new Promise(resolve => { cancelStarted = resolve; });
  const body = new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array([1])); }, cancel() { cancelStarted(); return cancellation; } });
  const instance = new Installation({ storage: { async get() { return identity; } } }, { ALLOW_TEST_TIMEOUT: 'true', BODY_TIMEOUT_MS: '100' });
  instance.socket = { send() { throw Error('must not reach backend'); } };
  const upload = new Request('https://internal/forward', { method: 'POST', headers: { 'x-relay-device-hash': deviceHash, 'x-relay-method': 'POST', 'x-relay-target': '/api/audio/transcribe' }, body, duplex: 'half' });
  const pending = instance.fetch(upload); await started;
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(instance.httpBusy, true);
  assert.equal((await pending).status, 408, 'deadline response is not blocked on cancellation');
  assert.equal(instance.httpBusy, true);
  finishCancel(); await new Promise(resolve => setImmediate(resolve)); assert.equal(instance.httpBusy, false);
});

test('stalled backend response deadline cancels source and frees admission', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let cancelled = false;
  const instance = new Installation({ storage: { async get() { return identity; } } }, { ALLOW_TEST_TIMEOUT: 'true', REQUEST_TIMEOUT_MS: '1000' });
  instance.socket = { send() {} };
  const pending = instance.fetch(request());
  while (!instance.pending.size) await new Promise(resolve => setImmediate(resolve));
  const body = new ReadableStream({ cancel() { cancelled = true; } });
  [...instance.pending.values()][0](new Response(body, { status: 500 }), true);
  const response = await pending;
  assert.equal(instance.httpBusy, true);
  t.mock.timers.tick(30000);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(cancelled, true); assert.equal(instance.httpBusy, false);
  await assert.rejects(response.arrayBuffer(), /response deadline/);
});

test('response deadline reports immediately but holds lease until cancellation settles', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let finishCancel;
  const cancellation = new Promise(resolve => { finishCancel = resolve; });
  const instance = new Installation({ storage: { async get() { return identity; } } }, { ALLOW_TEST_TIMEOUT: 'true', REQUEST_TIMEOUT_MS: '1000' });
  instance.socket = { send() {} };
  const pending = instance.fetch(request());
  while (!instance.pending.size) await new Promise(resolve => setImmediate(resolve));
  [...instance.pending.values()][0](new Response(new ReadableStream({ cancel() { return cancellation; } })), true);
  const response = await pending; t.mock.timers.tick(30000);
  await assert.rejects(response.arrayBuffer(), /response deadline/);
  assert.equal(instance.httpBusy, true);
  finishCancel(); await new Promise(resolve => setImmediate(resolve)); assert.equal(instance.httpBusy, false);
});
