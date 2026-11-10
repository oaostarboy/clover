import test from 'node:test';
import assert from 'node:assert/strict';
import { FlowSocket } from '../src/flow-socket.mjs';
import { MemoryLedger } from '../src/memory-ledger.mjs';
class Wire extends EventTarget {
  constructor() { super(); this.readyState = 1; this.sent = []; }
  send(d) { this.sent.push(d); }
  close() { this.readyState = 3; this.dispatchEvent(new Event('close')); }
  deliver(d) { this.dispatchEvent(new MessageEvent('message', { data: d })); }
}
const hello = w => w.deliver(JSON.stringify({ v: 1, t: 'hello', g: 'a'.repeat(32) }));
// Mirrors the relay: per-channel sends over one shared link socket; a channel that
// floods past the ledger must fail by itself and leave the link and others usable.
test('a flooding channel cannot close the shared link; a second channel keeps working', () => {
  const ledger = new MemoryLedger(64 * 1024), raw = new Wire();
  const link = new FlowSocket(raw, { reserve: w => ledger.reserve(w), closeOnCapacity: false });
  hello(raw);
  const ended = new Set(); let sentOk = 0;
  const channelSend = (ch, payload) => { try { link.send(JSON.stringify({ ch, payload })); return true; } catch { ended.add(ch); return false; } };
  const big = 'x'.repeat(20000);
  for (let i = 0; i < 1000 && !ended.has('flood'); i++) channelSend('flood', i % 2 ? big : 'up');
  for (let i = 0; i < 1000; i++) if (ended.has('flood')) break; else channelSend('flood', big);
  assert.ok(ended.has('flood'), 'flooding channel is ended');
  assert.equal(link.readyState, 1, 'link stays open');
  assert.equal(raw.readyState, 1);
  // drain the link so the second channel can send
  link.queue.splice(0).forEach(i => i.release()); link.queuedBytes = 0;
  link.flow.tx = null; link.flow.txRelease?.(); link.flow.txRelease = null; link.flow.pending = null;
  assert.equal(ledger.used, 0);
  assert.equal(channelSend('ok', 'hello'), true);
  assert.equal(ended.has('ok'), false);
  assert.equal(link.readyState, 1);
});
test('default FlowSocket still fails closed on memory capacity', () => {
  const ledger = new MemoryLedger(4), raw = new Wire();
  const s = new FlowSocket(raw, { reserve: w => ledger.reserve(w) }); hello(raw);
  assert.throws(() => s.send('ab'), /memory capacity/); assert.equal(s.readyState, 3);
});
