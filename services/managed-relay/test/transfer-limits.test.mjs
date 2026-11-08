import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer, request as httpRequest } from 'node:http';
import { createHash, randomBytes } from 'node:crypto';
import { buildSync } from 'esbuild';
import { Miniflare } from 'miniflare';
import WebSocket, { WebSocketServer } from 'ws';
import { FlowSocket } from '../src/flow-socket.mjs';
import { ensureEnrolled, openConnector } from '../src/desktop-connector.mjs';

const sha = b => createHash('sha256').update(b).digest('hex');

test('8 MiB audio round trip preserves bytes and rejects excess before backend', { timeout: 120000 }, async t => {
  buildSync({ entryPoints: ['src/worker.mjs'], outfile: 'dist/transfer-worker.mjs', bundle: true, format: 'esm', platform: 'neutral' });
  const bundle = await readFile('dist/transfer-worker.mjs', 'utf8');
  const marker = 'this.used += weight;';
  assert.equal(bundle.split(marker).length, 2, 'ledger instrumentation marker must be unique');
  await writeFile('dist/transfer-worker.mjs', bundle.replace(marker, `${marker} if (this.used > (this.testPeak || 0)) { this.testPeak = this.used; console.log('TEST-HTTP-LEDGER-PEAK', this.testPeak); }`));
  t.after(() => rm('dist/transfer-worker.mjs', { force: true }));
  const mf = new Miniflare({ workers: [{ name: 'relay', modules: true, scriptPath: 'dist/transfer-worker.mjs', compatibilityDate: '2026-07-30', bindings: { ENROLLMENT_ENABLED: 'true', REQUEST_TIMEOUT_MS: '120000' }, durableObjects: { INSTALLATIONS: { className: 'Installation', useSQLite: true }, DIRECTORY: { className: 'Directory', useSQLite: true }, GUARD: { className: 'Guard', useSQLite: true } } }] });
  t.after(() => mf.dispose()); const origin = (await mf.ready).origin;
  const dir = await mkdtemp(join(tmpdir(), 'relay-transfer-')); t.after(() => rm(dir, { recursive: true, force: true }));
  const { identity } = await ensureEnrolled(origin, join(dir, 'identity'), { allowInsecureLocalhost: true });
  const token = 'cdt_' + randomBytes(32).toString('hex');
  const input = randomBytes(8 * 1024 * 1024), output = randomBytes(8 * 1024 * 1024);
  let count = 0, receivedHash;
  const backend = createServer(async (req, res) => { count++; const hash = createHash('sha256'); for await (const chunk of req) hash.update(chunk); receivedHash = hash.digest('hex'); res.writeHead(200, { 'content-type': 'application/octet-stream' }); res.end(output); });
  await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve)); t.after(() => new Promise(resolve => backend.close(resolve)));
  const connector = await openConnector({ relayOrigin: origin, identity, backendOrigin: `http://127.0.0.1:${backend.address().port}`, allowInsecureLocalhost: true }); t.after(() => connector.close());
  await connector.replaceDevices([{ hash: sha(token), expiresAt: Date.now() + 180000 }]);
  const headers = { authorization: `Bearer ${token}`, 'content-type': 'application/octet-stream' };
  // Declared oversize is rejected from headers. Do not race a multi-megabyte
  // upload against the server closing the connection after its early 413.
  const excess = await new Promise((resolve, reject) => {
    const req = httpRequest(origin + '/api/audio/transcribe', { method: 'POST', headers: { ...headers, 'content-length': String(input.length + 1) }, timeout: 15000 }, res => { res.resume(); resolve(res.statusCode); });
    req.on('timeout', () => req.destroy(Error('oversize response deadline'))); req.on('error', reject); req.flushHeaders();
  });
  assert.equal(excess, 413); assert.equal(count, 0);
  const response = await fetch(origin + '/api/audio/transcribe', { method: 'POST', headers, body: input, signal: AbortSignal.timeout(115000) });
  const actual = Buffer.from(await response.arrayBuffer());
  assert.equal(response.status, 200, `response ${response.status}, backend requests ${count}: ${actual.subarray(0, 512).toString()}`);
  assert.equal(response.headers.get('content-type'), 'application/octet-stream');
  assert.equal(actual.length, output.length); assert.equal(sha(actual), sha(output)); assert.equal(receivedHash, sha(input)); assert.equal(count, 1);

  // Exercise cancellation through an actual HTTP socket, not a direct stream.
  // The connector receives the full backend response; the phone consumes only
  // its first network chunk before disconnecting during the 8 MiB response.
  let partialBytes = 0;
  await new Promise((resolve, reject) => {
    const req = httpRequest(origin + '/api/sessions', { headers, timeout: 15000 }, res => {
      assert.equal(res.statusCode, 200);
      res.once('data', chunk => {
        partialBytes = chunk.length;
        res.pause(); res.destroy(); req.destroy(); resolve();
      });
      res.on('error', error => { if (!partialBytes) reject(error); });
    });
    req.on('timeout', () => req.destroy(Error('partial response deadline')));
    req.on('error', error => { if (!partialBytes) reject(error); });
    req.end();
  });
  assert.ok(partialBytes > 0 && partialBytes < output.length);
  assert.equal(count, 2, 'cancelled exchange executes exactly once');
  // Retry only a side-effect-free test GET after explicit admission rejection.
  const recoveryDeadline = Date.now() + 5000;
  let recovered;
  while (Date.now() < recoveryDeadline) {
    recovered = await fetch(origin + '/api/sessions', { headers, signal: AbortSignal.timeout(15000) });
    if (recovered.status !== 503) break;
    await recovered.arrayBuffer();
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  assert.equal(recovered?.status, 200, 'live partial-response cancellation releases HTTP admission');
  const recoveryBytes = Buffer.from(await recovered.arrayBuffer());
  assert.equal(sha(recoveryBytes), sha(output));
  assert.equal(count, 3, 'cancelled request is not replayed and rejected GETs never execute');
  t.diagnostic(`live response cancelled after ${partialBytes} of ${output.length} bytes; next transfer completed`);
});

test('slow phone receives ordered backend messages without drops', { timeout: 80000 }, async t => {
  buildSync({ entryPoints: ['src/worker.mjs'], outfile: 'dist/transfer-slow-worker.mjs', bundle: true, format: 'esm', platform: 'neutral' });
  const originalBundle = await readFile('dist/transfer-slow-worker.mjs', 'utf8');
  for (const marker of ['this.tx = { bytes, binary, message: nonce(), offset: 0, index: 0 };', 'this.queuedBytes += bytes;', 'this.channels.delete(id);', 'this.queue.length = 0;\n    this.queuedBytes = 0;\n    this.flow.close();']) assert.ok(originalBundle.includes(marker), `missing test instrumentation marker: ${marker}`);
  const instrumented = originalBundle
    .replace('this.tx = { bytes, binary, message: nonce(), offset: 0, index: 0 };', 'this.peakTx = Math.max(this.peakTx || 0, bytes.length); this.tx = { bytes, binary, message: nonce(), offset: 0, index: 0 };')
    .replace('this.channels.delete(id);', 'this.channels.delete(id); console.log("TEST-WORKER-CHANNELS-REMAINING", this.channels.size);')
    .replace('this.queuedBytes += bytes;', 'this.queuedBytes += bytes; this.peakQueue = Math.max(this.peakQueue || 0, this.queuedBytes); if (this.queuedBytes > this.queueBytes) throw Error("test queue bound");')
    .replace('this.queue.length = 0;\n    this.queuedBytes = 0;\n    this.flow.close();', 'console.log("TEST-WORKER-FLOW", this.maxMessageBytes, this.peakQueue || 0, this.flow.peakTx || 0); this.queue.length = 0;\n    this.queuedBytes = 0;\n    this.flow.close();');
  await writeFile('dist/transfer-slow-worker.mjs', instrumented);
  t.after(() => rm('dist/transfer-slow-worker.mjs', { force: true }));
  let pauses = 0, resumes = 0, peakQueue = 0, peakInflight = 0;
  const originalPause = WebSocket.prototype.pause, originalResume = WebSocket.prototype.resume, originalSend = FlowSocket.prototype.send;
  WebSocket.prototype.pause = function(...args) { pauses++; return originalPause.apply(this, args); };
  WebSocket.prototype.resume = function(...args) { resumes++; return originalResume.apply(this, args); };
  FlowSocket.prototype.send = function(...args) { const result = originalSend.apply(this, args); peakQueue = Math.max(peakQueue, this.queuedBytes); peakInflight = Math.max(peakInflight, this.flow.tx?.bytes.length || 0); return result; };
  t.after(() => { WebSocket.prototype.pause = originalPause; WebSocket.prototype.resume = originalResume; FlowSocket.prototype.send = originalSend; });
  const mf = new Miniflare({ workers: [{ name: 'relay', modules: true, scriptPath: 'dist/transfer-slow-worker.mjs', compatibilityDate: '2026-07-30', bindings: { ENROLLMENT_ENABLED: 'true' }, durableObjects: { INSTALLATIONS: { className: 'Installation', useSQLite: true }, DIRECTORY: { className: 'Directory', useSQLite: true }, GUARD: { className: 'Guard', useSQLite: true } } }] });
  t.after(() => mf.dispose()); const origin = (await mf.ready).origin;
  const dir = await mkdtemp(join(tmpdir(), 'relay-slow-')); t.after(() => rm(dir, { recursive: true, force: true }));
  const { identity } = await ensureEnrolled(origin, join(dir, 'identity'), { allowInsecureLocalhost: true });
  const token = 'cdt_' + randomBytes(32).toString('hex'), ticket = randomBytes(32).toString('base64url');
  const backend = createServer((req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ ticket, ttl_seconds: 30 })); });
  const wss = new WebSocketServer({ noServer: true, handleProtocols: protocols => protocols.has('clover-gateway-v1') ? 'clover-gateway-v1' : false });
  backend.on('upgrade', (req, socket, head) => wss.handleUpgrade(req, socket, head, ws => wss.emit('connection', ws)));
  let local; const connected = new Promise(resolve => wss.once('connection', ws => { local = ws; resolve(); }));
  await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { for (const ws of wss.clients) ws.terminate(); backend.close(resolve); }));
  const connector = await openConnector({ relayOrigin: origin, identity, backendOrigin: `http://127.0.0.1:${backend.address().port}`, allowInsecureLocalhost: true }); t.after(() => connector.close());
  await connector.replaceDevices([{ hash: sha(token), expiresAt: Date.now() + 60000 }]);
  const minted = await fetch(origin + '/api/auth/ws-ticket', { method: 'POST', headers: { authorization: `Bearer ${token}` } }); assert.equal(minted.status, 200);
  const dial = new URL('/api/ws', origin); dial.protocol = 'ws:';
  const raw = new WebSocket(dial, ['clover-relay-flow-v1', 'clover-gateway-v1', `clover-gateway-ticket.${(await minted.json()).ticket}`], { maxPayload: 23000 }); t.after(() => raw.terminate());
  await new Promise((resolve, reject) => { raw.once('open', resolve); raw.once('error', reject); }); await connected;
  const phone = new FlowSocket(raw); t.after(() => phone.close());
  let release; const hold = new Promise(resolve => { release = resolve; }); const messages = []; let finish;
  const received = new Promise(resolve => { finish = resolve; });
  phone.addEventListener('message', async ({data}) => { messages.push(sha(Buffer.from(data))); if (messages.length === 1) await hold; if (messages.length === 20) finish(); });
  const payloads = Array.from({ length: 20 }, (_, i) => { const b = Buffer.alloc(128 * 1024, i); b.writeUInt32BE(i); return b; });
  const producer = (async () => { for (const payload of payloads) { while (local.readyState === WebSocket.OPEN && local.bufferedAmount > 256 * 1024) await new Promise(r => setTimeout(r, 10)); if (local.readyState !== WebSocket.OPEN) break; await new Promise((resolve, reject) => local.send(payload, { binary: true }, e => e ? reject(e) : resolve())); } })();
  await new Promise(r => setTimeout(r, 2000)); release(); await producer;
  await Promise.race([received, new Promise((_, reject) => { const timer = setTimeout(() => reject(Error(`only ${messages.length} of 20 messages delivered`)), 12000); timer.unref(); })]);
  assert.deepEqual(messages, payloads.map(sha));
  assert.ok(pauses > 0 && resumes > 0, `pause=${pauses} resume=${resumes}`);
  assert.ok(peakQueue <= 1048576 && peakInflight <= 12582912);
  t.diagnostic(`connector queue peak=${peakQueue} bytes, in-flight peak=${peakInflight} bytes, pauses=${pauses}, resumes=${resumes}`);
  phone.close();
  const second = await ensureEnrolled(origin, join(dir, 'identity-2'), { allowInsecureLocalhost: true });
  const secondToken = 'cdt_' + randomBytes(32).toString('hex');
  const other = await openConnector({ relayOrigin: origin, identity: second.identity, backendOrigin: `http://127.0.0.1:${backend.address().port}`, allowInsecureLocalhost: true }); t.after(() => other.close());
  await other.replaceDevices([{ hash: sha(secondToken), expiresAt: Date.now() + 90000 }]);
  const secondConnection = new Promise(resolve => wss.once('connection', resolve));
  const again = await fetch(origin + '/api/auth/ws-ticket', { method: 'POST', headers: { authorization: `Bearer ${token}` } }); assert.equal(again.status, 200);
  const stalledRaw = new WebSocket(dial, ['clover-relay-flow-v1', 'clover-gateway-v1', `clover-gateway-ticket.${(await again.json()).ticket}`], { maxPayload: 23000 }); t.after(() => stalledRaw.terminate());
  await new Promise((resolve, reject) => { stalledRaw.once('open', resolve); stalledRaw.once('error', reject); });
  const stalledLocal = await secondConnection;
  const stalledPhone = new FlowSocket(stalledRaw); t.after(() => stalledPhone.close());
  stalledPhone.addEventListener('message', () => new Promise(() => {}));
  const closed = new Promise(resolve => stalledLocal.once('close', resolve));
  const start = Date.now(); stalledLocal.send(Buffer.alloc(128 * 1024, 123));
  await Promise.race([closed, new Promise((_, reject) => { const timer = setTimeout(() => reject(Error('stalled local socket was not closed')), 38000); timer.unref(); })]);
  assert.ok(Date.now() - start < 38000);
  const healthy = await fetch(origin + '/api/sessions', { headers: { authorization: `Bearer ${secondToken}` }, signal: AbortSignal.timeout(5000) });
  assert.equal(healthy.status, 200, 'other installation remains available after stall');
  t.diagnostic(`stalled local socket closed after ${Date.now() - start} ms; second installation responded ${healthy.status}`);
});
