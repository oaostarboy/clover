import test from 'node:test';
import assert from 'node:assert/strict';
import { MemoryLedger } from '../src/memory-ledger.mjs';

test('reservations share a hard ceiling and release exactly once', () => {
  const ledger = new MemoryLedger(100);
  const a = ledger.reserve(60);
  assert.ok(a);
  assert.equal(ledger.used, 60);
  assert.equal(ledger.reserve(41), null);
  assert.equal(ledger.used, 60);
  const b = ledger.reserve(40);
  assert.ok(b);
  a(); a();
  assert.equal(ledger.used, 40);
  b();
  assert.equal(ledger.used, 0);
});
