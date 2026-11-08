import test from 'node:test';
import assert from 'node:assert/strict';
import { FlowSocket } from '../src/flow-socket.mjs';
class Wire extends EventTarget {
  constructor() { super(); this.sent = []; this.readyState = 1; }
  send(data) { this.sent.push(data); }
  close() { this.readyState = 3; this.dispatchEvent(new Event('close')); }
  deliver(data) { this.dispatchEvent(new MessageEvent('message', { data })); }
}
test('flow socket bounds queued messages before the peer grants credit', t => {
  const raw = new Wire(); const ws = new FlowSocket(raw, { queueBytes: 8, queueCount: 2 }); t.after(() => ws.close());
  ws.send('1234'); ws.send('5678');
  assert.equal(ws.queuedBytes, 8); assert.throws(() => ws.send('9'), /capacity/);
  assert.equal(raw.sent.length, 1); // Only hello; no peer credit yet.
  ws.close(); assert.equal(ws.queuedBytes, 0);
});
test('configured connector accepts one large message but not a second while busy', t => {
  const raw = new Wire(); const ws = new FlowSocket(raw, { maxMessageBytes: 12582912 }); t.after(() => ws.close());
  raw.deliver(JSON.stringify({ v: 1, t: 'hello', g: 'a'.repeat(32) }));
  ws.send('x'.repeat(1048577));
  assert.equal(ws.bufferedAmount, 1048577);
  assert.throws(() => ws.send('x'.repeat(1048577)), /queue capacity/);
  assert.throws(() => new FlowSocket(new Wire(), { maxMessageBytes: 12582913 }), /budget/);
});
