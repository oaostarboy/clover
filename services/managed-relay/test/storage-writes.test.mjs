import test from 'node:test';
import assert from 'node:assert/strict';
import { Installation, Directory } from '../src/worker.mjs';

const hash = 'b'.repeat(64);
function fakeState(initial = {}) {
  const data = new Map(Object.entries(initial)); const writes = [];
  return { writes, data, blockConcurrencyWhile: fn => fn(), storage: {
    async get(k) { return data.get(k); },
    async put(k, v) { if (typeof k === 'object') { for (const [a, b] of Object.entries(k)) { data.set(a, b); writes.push(a); } } else { data.set(k, v); writes.push(k); } },
    async delete(k) { data.delete(k); writes.push('del:' + k); },
    async setAlarm() { writes.push('alarm'); } } };
}

test('unchanged device-list sync performs zero storage writes (Installation and Directory)', async () => {
  const expiresAt = Date.now() + 60000;
  const dirState = fakeState(); const dir = new Directory(dirState, {});
  const env = { DIRECTORY: { idFromName: n => n, get: () => ({ fetch: (u, i) => dir.fetch(new Request(u, i)) }) } };
  const state = fakeState({ identity: { id: 'x'.repeat(32), secretHash: 'h', devices: [] } });
  const inst = new Installation(state, env);
  await inst.replaceDevices({ devices: [{ hash, expiresAt }] }, 0);
  assert.ok(state.writes.length >= 1 && dirState.writes.length >= 1, 'first sync persists');
  state.writes.length = 0; dirState.writes.length = 0;
  for (let i = 0; i < 5; i++) await inst.replaceDevices({ devices: [{ hash, expiresAt }] }, 0);
  assert.deepEqual(state.writes, []); assert.deepEqual(dirState.writes, []);
  await inst.replaceDevices({ devices: [{ hash, expiresAt: expiresAt + 1000 }] }, 0);
  assert.ok(state.writes.length >= 1, 'a changed list still persists');
});

test('Directory claim with identical owner/expiry does not write', async () => {
  const s = fakeState(); const dir = new Directory(s, {}); const exp = Date.now() + 60000;
  const claim = () => dir.fetch(new Request('https://internal/claim', { method: 'POST', body: JSON.stringify({ id: 'a', expiresAt: exp, revision: crypto.randomUUID() }) }));
  assert.equal((await claim()).status, 200); const n = s.writes.length; assert.equal(n, 1);
  assert.equal((await claim()).status, 200); assert.equal(s.writes.length, n);
});
