import test from 'node:test';
import assert from 'node:assert/strict';
import worker, { Guard } from '../src/worker.mjs';

const envWith = guardFetch => ({ GUARD: { idFromName: n => n, get: () => ({ fetch: guardFetch }) } });
const req = () => new Request('https://relay.example.test/x', { headers: { 'CF-Connecting-IP': '192.0.2.9' } });

test('Guard that throws fails open', async () => {
  const r = await worker.handle(req(), envWith(async () => { throw new Error('boom'); }));
  assert.equal(r.status, 404);
});
test('Guard that hangs fails open after timeout', async () => {
  const t = Date.now();
  const r = await worker.handle(req(), envWith(() => new Promise(() => {})));
  assert.equal(r.status, 404);
  assert.ok(Date.now() - t < 3000);
});
test('explicit 429 from Guard still rate limits', async () => {
  const r = await worker.handle(req(), envWith(async () => new Response('{}', { status: 429 })));
  assert.equal(r.status, 429);
});
test('Guard /rate returns 429 above the limit without storage', async () => {
  const state = { blockConcurrencyWhile: () => { throw new Error('must not be used'); }, storage: new Proxy({}, { get() { throw new Error('no storage'); } }) };
  const g = new Guard(state, { MAX_REQUESTS_PER_IP_MINUTE: '3' });
  const hit = () => g.fetch(new Request('https://internal/rate', { method: 'POST', body: JSON.stringify({ ip: 'a' }) }));
  const s = []; for (let i = 0; i < 5; i++) s.push((await hit()).status);
  assert.deepEqual(s, [200, 200, 200, 429, 429]);
});
