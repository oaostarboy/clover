import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:http';
import { createHash, randomBytes } from 'node:crypto';
import { buildSync } from 'esbuild';
import { Miniflare } from 'miniflare';
import WebSocket from 'ws';
import { ensureEnrolled, openConnector } from '../src/desktop-connector.mjs';

test('late live ticket claim retains admission then cancels the timed-out ticket', { timeout: 15000 }, async t => {
  const file = 'dist/live-ticket-delay-worker.mjs';
  buildSync({ entryPoints: ['src/worker.mjs'], outfile: file, bundle: true, format: 'esm', platform: 'neutral' });
  const bundle = await readFile(file, 'utf8');
  const marker = 'if (path === "/ticket-claim") {';
  assert.equal(bundle.split(marker).length, 2, 'delay only the real Directory ticket-claim handler');
  // Test-only delay in the actual workerd Durable Object, not a fake claim result.
  await writeFile(file, bundle.replace(marker, marker + '\n await new Promise(resolve => setTimeout(resolve, 1500));'));
  t.after(() => rm(file, { force: true }));
  const mf = new Miniflare({ workers: [{ name: 'relay', modules: true, scriptPath: file, compatibilityDate: '2026-07-30', bindings: { ENROLLMENT_ENABLED: 'true', REQUEST_TIMEOUT_MS: '500', ALLOW_TEST_TIMEOUT: 'true' }, durableObjects: { INSTALLATIONS: { className: 'Installation', useSQLite: true }, DIRECTORY: { className: 'Directory', useSQLite: true }, GUARD: { className: 'Guard', useSQLite: true } } }] });
  t.after(() => mf.dispose()); const origin = (await mf.ready).origin;
  const dir = await mkdtemp(join(tmpdir(), 'relay-late-ticket-')); t.after(() => rm(dir, { recursive: true, force: true }));
  const { identity } = await ensureEnrolled(origin, join(dir, 'identity'), { allowInsecureLocalhost: true });
  const token = 'cdt_' + randomBytes(32).toString('hex'), ticket = randomBytes(32).toString('base64url');
  let minted = 0, health = 0, upgrades = 0;
  const backend = createServer((req, res) => {
    res.writeHead(200, { 'content-type': 'application/json' });
    if (req.url === '/api/auth/ws-ticket') { minted++; res.end(JSON.stringify({ ticket, ttl_seconds: 30 })); }
    else { health++; res.end('{"ok":true}'); }
  });
  backend.on('upgrade', (_req, socket) => { upgrades++; socket.destroy(); });
  await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve));
  t.after(() => { backend.closeAllConnections(); backend.close(); });
  const connector = await openConnector({ relayOrigin: origin, identity, backendOrigin: `http://127.0.0.1:${backend.address().port}`, allowInsecureLocalhost: true });
  t.after(() => connector.close());
  await connector.replaceDevices([{ hash: createHash('sha256').update(token).digest('hex'), expiresAt: Date.now() + 60000 }]);
  const headers = { Authorization: `Bearer ${token}` };
  const timedOut = await fetch(origin + '/api/auth/ws-ticket', { method: 'POST', headers, signal: AbortSignal.timeout(5000) });
  assert.equal(timedOut.status, 504); await timedOut.arrayBuffer(); assert.equal(minted, 1);
  const held = await fetch(origin + '/api/sessions', { headers });
  assert.equal(held.status, 503, 'lease remains held while real ticket claim is unsettled');
  await held.arrayBuffer(); assert.equal(health, 0);
  let recovered;
  const deadline = Date.now() + 5000;
  do {
    await new Promise(resolve => setTimeout(resolve, 50));
    recovered = await fetch(origin + '/api/sessions', { headers, signal: AbortSignal.timeout(2000) });
    await recovered.arrayBuffer();
  } while (recovered.status === 503 && Date.now() < deadline);
  assert.equal(recovered.status, 200); assert.equal(health, 1); assert.equal(minted, 1);
  const dial = new URL('/api/ws', origin); dial.protocol = 'ws:';
  const raw = new WebSocket(dial, ['clover-relay-flow-v1', 'clover-gateway-v1', `clover-gateway-ticket.${ticket}`], { handshakeTimeout: 2000 });
  const status = await new Promise((resolve, reject) => {
    raw.once('unexpected-response', (req, res) => { res.resume(); req.destroy(); resolve(res.statusCode); });
    raw.once('open', () => { raw.terminate(); reject(Error('timed-out ticket upgraded')); });
    raw.once('error', reject);
  });
  assert.equal(status, 401); assert.equal(upgrades, 0, 'late ticket never reaches backend WebSocket');
});
