import test from 'node:test';
import assert from 'node:assert/strict';
import { FlowTransport } from '../src/flow.mjs';

function pair(onA = (_, release) => release(), onB = (_, release) => release(), options = {}) {
  const ab = [], ba = [], errors = [];
  const a = new FlowTransport({ ...options, send: x => ab.push(x), onMessage: onA, onClose: x => errors.push(x) });
  const b = new FlowTransport({ ...options, send: x => ba.push(x), onMessage: onB, onClose: x => errors.push(x) });
  a.start(); b.start();
  const pump = () => { let n = 0; while (ab.length || ba.length) { if (++n > 10000) throw Error('loop'); if (ab.length) b.receive(ab.shift()); if (ba.length) a.receive(ba.shift()); } };
  pump();
  return { a, b, ab, ba, errors, pump, close() { a.close(); b.close(); } };
}

test('flow reserves before copying outgoing data and releases on close', t => {
  let used = 0;
  const p = pair(undefined, undefined, { reserve: weight => {
    if (used + weight > 64) return null;
    used += weight;
    let released = false;
    return () => { if (!released) { released = true; used -= weight; } };
  } });
  t.after(() => p.close());
  assert.equal(p.a.trySend(new Uint8Array(65)), false);
  assert.equal(used, 0);
  assert.equal(p.a.trySend(new Uint8Array(32)), true);
  assert.ok(used >= 32);
  p.a.close();
  assert.equal(used, 0);
});

test('flow holds receive reservation through downstream release and frees on close', t => {
  let held = 0, releaseMessage;
  const reserve = weight => { held += weight; let done = false; return () => { if (!done) { done = true; held -= weight; } }; };
  const outgoing = [], incoming = [];
  const a = new FlowTransport({ send: x => outgoing.push(x), onMessage() {} });
  const b = new FlowTransport({ send: x => incoming.push(x), onMessage: (_, release) => { releaseMessage = release; }, reserve });
  t.after(() => { a.close(); b.close(); });
  a.start(); b.start(); b.receive(outgoing.shift()); a.receive(incoming.shift());
  assert.equal(a.trySend(new Uint8Array(20)), true);
  b.receive(outgoing.shift());
  assert.ok(held >= 20);
  releaseMessage();
  assert.equal(held, 0);
  a.receive(incoming.shift());
  assert.equal(a.trySend(new Uint8Array(20)), true);
  b.receive(outgoing.shift());
  assert.ok(held >= 20);
  b.close();
  assert.ok(held >= 20); // The asynchronous consumer still owns the payload.
  releaseMessage();
  assert.equal(held, 0);
});

test('flow refuses declared receive allocation when the reservation fails', t => {
  const outgoing = [], incoming = [];
  let received = false;
  const a = new FlowTransport({ send: x => outgoing.push(x), onMessage() {} });
  const b = new FlowTransport({ send: x => incoming.push(x), onMessage: () => { received = true; }, reserve: () => null });
  t.after(() => { a.close(); b.close(); });
  a.start(); b.start(); b.receive(outgoing.shift()); a.receive(incoming.shift());
  assert.equal(a.trySend(new Uint8Array(20)), true);
  b.receive(outgoing.shift());
  assert.equal(b.closed, true);
  assert.equal(b.rx, null);
  assert.equal(received, false);
});

test('flow rejects data before the peer hello handshake', t => {
  const sent = []; const received = [];
  const f = new FlowTransport({ send: x => sent.push(x), onMessage: x => received.push(x) });
  t.after(() => f.close()); f.start();
  const grant = JSON.parse(sent[0]).g;
  f.receive(JSON.stringify({ v: 1, t: 'data', g: grant, n: 'a'.repeat(32), m: 'b'.repeat(32), i: 0, z: 1, b: false, f: true, d: 'eA==' }));
  assert.equal(f.closed, true);
  assert.deepEqual(received, []);
});

test('connector flow can opt into a bounded larger envelope without changing phone defaults', t => {
  const seen = []; const p = pair(undefined, (data, release) => { seen.push(data.length); release(); }, { maxMessageBytes: 12 * 1048576 }); t.after(() => p.close());
  assert.equal(p.a.trySend(new Uint8Array(1048577)), true); p.pump(); assert.deepEqual(seen, [1048577]);
  assert.throws(() => new FlowTransport({send(){},onMessage(){},maxMessageBytes: 12 * 1048576 + 1}), /budget/);
});

test('flow rejects unnegotiated control fields and padded control frames', t => {
  for (const raw of [JSON.stringify({v:1,t:'hello',g:'a'.repeat(32),extra:'unnegotiated'}), ' '.repeat(200) + JSON.stringify({v:1,t:'hello',g:'a'.repeat(32)})]) {
    const f = new FlowTransport({ send() {}, onMessage() {} }); t.after(() => f.close()); f.start(); f.receive(raw); assert.equal(f.closed, true);
  }
});

test('flow rejects forged or duplicate receipts without granting additional sends', t => {
  for (const forged of [true, false]) {
    const p = pair(); t.after(() => p.close());
    assert.equal(p.a.trySend('one'), true); const wire = p.ab.shift(); p.b.receive(wire);
    const ack = p.ba.shift();
    if (forged) { const m = JSON.parse(ack); m.n = '0'.repeat(32); p.a.receive(JSON.stringify(m)); }
    else { p.a.receive(ack); p.a.receive(ack); }
    assert.equal(p.a.closed, true); assert.equal(p.a.trySend('two'), false); assert.equal(p.ab.length, 0);
  }
});

test('flow requires proof of receipt before accepting another chunk', t => {
  const p = pair(); t.after(() => p.close());
  p.a.trySend(new Uint8Array(32768)); const wire = p.ab.shift(); p.b.receive(wire);
  const attack = JSON.parse(wire); attack.i = 1; attack.f = true;
  p.b.receive(JSON.stringify(attack)); assert.equal(p.b.closed, true);
  assert.equal(p.ba.length, 1); // No response amplification from invalid frames.
  for (let i = 0; i < 1000; i++) p.b.receive(wire);
  assert.equal(p.ba.length, 1);
});

test('flow rejects malformed payloads and frees all state', t => {
  const edits = [m => { m.d = '***'; }, m => { m.z = 1048577; }, m => { m.i = 2; }, m => { m.f = false; }, m => { m.m = 'bad'; }];
  for (const edit of edits) {
    const p = pair(); t.after(() => p.close()); p.a.trySend('hi'); const m = JSON.parse(p.ab.shift()); edit(m);
    p.b.receive(JSON.stringify(m)); assert.equal(p.b.closed, true); assert.equal(p.b.rx, null); assert.equal(p.b.timers.size, 0);
  }
  const p = pair(); t.after(() => p.close()); p.b.receive(' '.repeat(23001)); assert.equal(p.b.closed, true);
});

test('flow preserves Unicode, empty and binary messages across repeated transfers', t => {
  const seenA = [], seenB = [];
  const p = pair((x,r) => { seenA.push(x); r(); }, (x,r) => { seenB.push(x); r(); }); t.after(() => p.close());
  const cases = ['', new Uint8Array(), '🍀'.repeat(10000), new Uint8Array([0,255,128])];
  for (let i = 0; i < 40; i++) {
    const x = cases[i % cases.length]; assert.equal(p.a.trySend(x), true); assert.equal(p.b.trySend(x), true); p.pump();
    assert.deepEqual(seenA.at(-1), x); assert.deepEqual(seenB.at(-1), x); assert.equal(p.a.pending, null);
  }
  assert.equal(seenA.length, 40); assert.equal(seenB.length, 40);
});

test('flow never grows its output while receipt is withheld', t => {
  const p = pair(); t.after(() => p.close());
  p.a.trySend(new Uint8Array(1048576));
  for (let i = 0; i < 10000; i++) assert.equal(p.a.trySend('extra'), false);
  assert.equal(p.ab.length, 1); assert.ok(p.ab[0].length <= 23000); assert.equal(p.a.tx.bytes.length, 1048576);
  p.a.close(); assert.equal(p.a.tx, null); assert.equal(p.a.pending, null); assert.equal(p.a.timers.size, 0);
});

test('flow aborts a peer that never acknowledges and frees the pending message', async t => {
  const p = pair(undefined, () => {}, { timeoutMs: 25 }); t.after(() => p.close());
  assert.equal(p.a.trySend('held'), true); p.pump();
  await new Promise(r => setTimeout(r, 60));
  assert.equal(p.a.closed, true); assert.equal(p.b.closed, true);
  assert.equal(p.a.tx, null); assert.equal(p.b.rx, null);
});

test('flow reassembles a large binary message through bounded chunks', t => {
  const seen = []; const p = pair(undefined, (data, release) => { seen.push(data); release(); }); t.after(() => p.close());
  const data = new Uint8Array(1024 * 1024); for (let i = 0; i < data.length; i++) data[i] = i % 251;
  assert.equal(p.a.trySend(data), true);
  let frames = 0;
  while (p.ab.length || p.ba.length) {
    if (p.ab.length) { const wire = p.ab.shift(); assert.ok(wire.length <= 23000); frames++; p.b.receive(wire); }
    if (p.ba.length) p.a.receive(p.ba.shift());
    assert.ok(frames <= 64);
  }
  assert.equal(frames, 64); assert.deepEqual(seen, [data]);
  assert.equal(p.a.trySend(new Uint8Array(1024 * 1024 + 1)), false);
});

test('flow sends one message and waits for downstream admission before accepting another', t => {
  let release; const seen = [];
  const p = pair(undefined, (data, done) => { seen.push(data); release = done; }); t.after(() => p.close());
  assert.equal(p.a.trySend('hello'), true); p.pump();
  assert.deepEqual(seen, ['hello']);
  assert.equal(p.a.trySend('second'), false);
  release(); p.pump();
  assert.equal(p.a.trySend('second'), true); p.pump();
  assert.deepEqual(seen, ['hello','second']);
});
