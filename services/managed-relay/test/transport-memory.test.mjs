import test from 'node:test';
import assert from 'node:assert/strict';
import { FlowSocket } from '../src/flow-socket.mjs';
import { MemoryLedger } from '../src/memory-ledger.mjs';
class Wire extends EventTarget {
  constructor() { super(); this.readyState = 1; this.sent = []; }
  send(data) { this.sent.push(data); }
  close() { this.readyState = 3; this.dispatchEvent(new Event('close')); }
  deliver(data) { this.dispatchEvent(new MessageEvent('message', { data })); }
}
test('two socket queues share one ledger and release on close', () => {
  const ledger = new MemoryLedger(10);
  const options = { reserve: weight => ledger.reserve(weight) };
  const a = new FlowSocket(new Wire(), options), b = new FlowSocket(new Wire(), options);
  try {
    a.send('abc');
    assert.equal(ledger.used, 9);
    assert.throws(() => b.send('x'), /memory capacity/); assert.equal(b.readyState, 3);
    a.close(); a.close();
    assert.equal(ledger.used, 0);

  } finally { a.close(); b.close(); }
  assert.equal(ledger.used, 0);
});
test('core reservation failure fails closed instead of queuing', () => {
  const ledger = new MemoryLedger(4), raw = new Wire();
  const socket = new FlowSocket(raw, { reserve: weight => ledger.reserve(weight) });
  try {
    raw.deliver(JSON.stringify({ v: 1, t: 'hello', g: 'a'.repeat(32) }));
    assert.throws(() => socket.send('ab'), /memory capacity/);
    assert.equal(socket.readyState, 3);
    assert.equal(ledger.used, 0);
  } finally { socket.close(); }
});
test('consumer rejection closes without acknowledging undelivered data', async () => {
  const ledger = new MemoryLedger(100), raw = new Wire();
  const socket = new FlowSocket(raw, { reserve: weight => ledger.reserve(weight) });
  const grant = JSON.parse(raw.sent[0]).g;
  raw.deliver(JSON.stringify({ v: 1, t: 'hello', g: 'a'.repeat(32) }));
  socket.addEventListener('message', async () => { throw Error('consumer failed'); });
  raw.deliver(JSON.stringify({ v: 1, t: 'data', g: grant, n: 'b'.repeat(32), m: 'c'.repeat(32), i: 0, z: 1, b: true, f: true, d: 'eA==' }));
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(socket.readyState, 3);
  assert.equal(ledger.used, 0);
  assert.equal(raw.sent.filter(x => JSON.parse(x).t === 'ack').length, 0);
});

test('two socket declarations cannot exceed the same ledger', () => {
  const ledger = new MemoryLedger(30000), wires = [new Wire(), new Wire()];
  const sockets = wires.map(w => new FlowSocket(w, { reserve: n => ledger.reserve(n) }));
  try {
    for (const raw of wires) {
      const grant = JSON.parse(raw.sent[0]).g;
      raw.deliver(JSON.stringify({ v: 1, t: 'hello', g: 'a'.repeat(32) }));
      raw.deliver(JSON.stringify({ v: 1, t: 'data', g: grant, n: 'b'.repeat(32), m: 'c'.repeat(32), i: 0, z: 20000, b: true, f: false, d: Buffer.alloc(16384).toString('base64') }));
    }
    assert.equal(ledger.used, 20000);
    assert.equal(sockets[0].readyState, 1); assert.equal(sockets[1].readyState, 3);
    sockets[0].close(); sockets[0].close(); assert.equal(ledger.used, 0);
  } finally { sockets.forEach(s => s.close()); }
});

test('queue and transmit reject reservation before copying bytes', () => {
  class NoCopy extends Uint8Array { slice() { throw Error('copy occurred'); } }
  for (const ready of [false, true]) {
    const raw = new Wire(), socket = new FlowSocket(raw, { reserve: () => null });
    if (ready) raw.deliver(JSON.stringify({ v: 1, t: 'hello', g: 'a'.repeat(32) }));
    assert.throws(() => socket.send(new NoCopy(10)), /memory capacity/);
    assert.equal(socket.readyState, 3);
  }
});

test('consumer retains reservation through timeout and close until finally unwinds', async () => {
  const ledger = new MemoryLedger(100), raw = new Wire();
  const socket = new FlowSocket(raw, { timeoutMs: 20, reserve: n => ledger.reserve(n) });
  const grant = JSON.parse(raw.sent[0]).g;
  let finish; const held = new Promise(resolve => { finish = resolve; });
  let ended = false;
  socket.addEventListener('message', async () => { try { await held; } finally { ended = true; } });
  raw.deliver(JSON.stringify({ v: 1, t: 'hello', g: 'a'.repeat(32) }));
  raw.deliver(JSON.stringify({ v: 1, t: 'data', g: grant, n: 'b'.repeat(32), m: 'c'.repeat(32), i: 0, z: 1, b: true, f: true, d: 'eA==' }));
  assert.equal(ledger.used, 1);
  await new Promise(resolve => setTimeout(resolve, 50));
  assert.equal(socket.readyState, 3); socket.close(); assert.equal(ledger.used, 1); assert.equal(ended, false);
  finish(); await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(ended, true); assert.equal(ledger.used, 0);
});

test('queued reservation overlaps core ownership then releases on final and duplicate ACK', () => {
  const ledger = new MemoryLedger(30), raw = new Wire(); let peak = 0;
  const socket = new FlowSocket(raw, { reserve: n => { const release = ledger.reserve(n); peak = Math.max(peak, ledger.used); return release; } });
  socket.send('abc'); assert.equal(ledger.used, 9);
  raw.deliver(JSON.stringify({ v: 1, t: 'hello', g: 'a'.repeat(32) }));
  assert.equal(ledger.used, 9); assert.equal(peak, 18);
  const frame = JSON.parse(raw.sent.at(-1));
  const ack = JSON.stringify({ v: 1, t: 'ack', n: frame.n, g: 'd'.repeat(32) });
  raw.deliver(ack); assert.equal(ledger.used, 0);
  raw.deliver(ack); socket.close(); assert.equal(ledger.used, 0);
});

test('queue drain closes and releases ownership when overlapping core reservation fails', () => {
  const ledger = new MemoryLedger(12), raw = new Wire(), socket = new FlowSocket(raw, { reserve: n => ledger.reserve(n) });
  socket.send('abc'); assert.equal(ledger.used, 9);
  raw.deliver(JSON.stringify({ v: 1, t: 'hello', g: 'a'.repeat(32) }));
  assert.equal(socket.readyState, 3); assert.equal(ledger.used, 0);
  socket.close(); assert.equal(ledger.used, 0);
});

test('UTF-8 queue sizing matches TextEncoder including lone surrogates', () => {
  const values = ['ASCII', '\ud800', '\udc00', '𝄞', 'é漢', '\ud800X\udc00'];
  for (const value of values) {
    const raw = new Wire(), socket = new FlowSocket(raw);
    socket.send(value); assert.equal(socket.queuedBytes, new TextEncoder().encode(value).length); socket.close();
  }
});
