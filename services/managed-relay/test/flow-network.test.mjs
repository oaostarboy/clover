import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { WebSocketServer, WebSocket } from 'ws';
import { FlowTransport } from '../src/flow.mjs';
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn) { for (let i = 0; i < 300; i++) { if (fn()) return; await sleep(10); } throw Error('fixture deadline'); }

test('real WebSocket pauses with one outstanding frame and resumes losslessly', { timeout: 15000 }, async t => {
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0, maxPayload: 23000 });
  await once(server, 'listening');
  let client, remote, a, b;
  t.after(async () => { a?.close(); b?.close(); client?.terminate(); remote?.terminate(); await new Promise(r => server.close(r)); });
  const connection = once(server, 'connection');
  client = new WebSocket(`ws://127.0.0.1:${server.address().port}`, { maxPayload: 23000 });
  [remote] = await connection; await once(client, 'open');
  let sent = 0, held, received = 0; const payload = new Uint8Array(1048576).fill(173);
  a = new FlowTransport({ send: raw => { sent++; assert.ok(raw.length <= 23000); client.send(raw); }, onMessage: (_, release) => release() });
  b = new FlowTransport({ send: raw => remote.send(raw), onMessage: (data, release) => { assert.deepEqual(data, payload); received++; held = release; } });
  client.on('message', data => a.receive(data.toString())); remote.on('message', data => b.receive(data.toString()));
  a.start(); b.start(); await until(() => !!a.peerGrant && !!b.peerGrant);
  remote.pause();
  assert.equal(a.trySend(payload), true); const before = sent;
  await sleep(100);
  assert.equal(sent, before); assert.equal(received, 0); assert.equal(a.trySend('cannot queue'), false);
  remote.resume(); await until(() => received === 1);
  assert.equal(a.trySend('still held'), false); held(); await until(() => !a.tx);
  for (let i = 0; i < 4; i++) { assert.equal(a.trySend(payload), true); await until(() => received === i + 2); held(); await until(() => !a.tx); }
  assert.equal(received, 5); assert.equal(a.closed, false); assert.equal(b.closed, false);
});
