// Explicit integration fixture: requires CLOVER_FIXTURE_PYTHON and CLOVER_PHONE_ADAPTER.
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { randomBytes, randomFillSync } from 'node:crypto';
import { build } from 'esbuild';
import { Miniflare } from 'miniflare';
import WebSocket from 'ws';
import { createDesktopPairing } from '../src/desktop-pairing.mjs';
import { ensureEnrolled, openConnector } from '../src/desktop-connector.mjs';

const python = process.env.CLOVER_FIXTURE_PYTHON;
const adapterPath = process.env.CLOVER_PHONE_ADAPTER;
if (!python || !adapterPath) throw Error('Set CLOVER_FIXTURE_PYTHON and CLOVER_PHONE_ADAPTER');
const { createRelaySocket } = await import(adapterPath);
const dir = await mkdtemp(join(tmpdir(), 'clover-relay-acceptance-'));
const ready = join(dir, 'ready.json');
const owner = randomBytes(32).toString('base64url');
const child = spawn(python, ['-m', 'clover_cli.main', 'serve', '--host', '127.0.0.1', '--port', '0', '--isolated', '--skip-build'], {
  cwd: resolve('../..'), env: { ...process.env, CLOVER_HOME: join(dir, 'home'), CLOVER_DESKTOP_READY_FILE: ready, CLOVER_DASHBOARD_SESSION_TOKEN: owner }, stdio: ['ignore', 'ignore', 'pipe']
});
let errors = ''; child.stderr.on('data', chunk => { errors += chunk; });
let mf, service, raw;
const event = (target, name) => new Promise(resolve => target.addEventListener(name, resolve, { once: true }));
const deadline = promise => Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(Error('fixture deadline')), 12000).unref())]);
try {
  let port;
  for (let i = 0; i < 300; i++) {
    try { port = JSON.parse(await readFile(ready, 'utf8')).port; break; } catch {}
    if (child.exitCode !== null) throw Error(`gateway exited ${child.exitCode}: ${errors.slice(-2000)}`);
    await new Promise(r => setTimeout(r, 100));
  }
  if (!port) throw Error('gateway not ready: ' + errors.slice(-2000));
  const backendOrigin = `http://127.0.0.1:${port}`;
  const out = join(dir, 'worker.mjs');
  await build({ entryPoints: ['src/worker.mjs'], outfile: out, bundle: true, format: 'esm', platform: 'neutral', logLevel: 'silent' });
  mf = new Miniflare({ workers: [{ name: 'relay', modules: true, script: await readFile(out, 'utf8'), compatibilityDate: '2026-07-30', bindings: { ENROLLMENT_ENABLED: 'true' }, durableObjects: { INSTALLATIONS: { className: 'Installation', useSQLite: true }, DIRECTORY: { className: 'Directory', useSQLite: true }, GUARD: { className: 'Guard', useSQLite: true } } }] });
  const relay = (await mf.ready).origin;
  const request = async (_connection, route, options = {}) => {
    const response = await fetch(backendOrigin + route, { ...options, headers: { 'X-Clover-Session-Token': owner } });
    if (!response.ok) throw Error(`gateway ${route}: ${response.status}`);
    return response.json();
  };
  service = createDesktopPairing({ identityPath: () => join(dir, 'identity.json'), request, syncIntervalMs: 0,
    enroll: (_origin, path) => ensureEnrolled(relay, path, { allowInsecureLocalhost: true }),
    connect: options => openConnector({ ...options, relayOrigin: relay, allowInsecureLocalhost: true }) });
  const connection = { mode: 'local', baseUrl: backendOrigin, token: owner };
  const pair = await service.pair('legacy', 'default', connection);
  const uri = new URL(pair.uri); const token = uri.searchParams.get('token');
  assert.equal(uri.searchParams.get('origin'), 'https://relay.cloverai.ca');
  assert.ok(token.startsWith('cdt_'));
  const headers = { Authorization: `Bearer ${token}` };
  const sessions = await fetch(relay + '/api/sessions', { headers }); assert.equal(sessions.status, 200); await sessions.json();
  const ticketResponse = await fetch(relay + '/api/auth/ws-ticket', { method: 'POST', headers }); assert.equal(ticketResponse.status, 200);
  const ticket = (await ticketResponse.json()).ticket;
  const url = new URL('/api/ws', relay); url.protocol = 'ws:';
  raw = new WebSocket(url, ['clover-relay-flow-v1', 'clover-gateway-v1', `clover-gateway-ticket.${ticket}`]);
  const phone = createRelaySocket(raw, { randomValues: a => randomFillSync(a) });
  const opened = event(phone, 'open'); const first = event(phone, 'message'); await deadline(opened); await deadline(first);
  async function rpc(id, method, params = {}) {
    const result = new Promise(resolve => { const handler = e => { let m; try { m = JSON.parse(e.data); } catch { return; } if (m.id === id) { phone.removeEventListener('message', handler); resolve(m); } }; phone.addEventListener('message', handler); });
    phone.send(JSON.stringify({ jsonrpc: '2.0', id, method, params })); return deadline(result);
  }
  const create = await rpc(1, 'session.create'); assert.ok(create.result?.session_id, JSON.stringify(create));
  const history = await rpc(2, 'session.history', { session_id: create.result.session_id }); assert.ok(history.result, JSON.stringify(history));
  const denied = await rpc(3, 'config.set'); assert.equal(denied.error?.code, 4030);
  const terminal = await rpc(4, 'terminal.resize', { session_id: create.result.session_id }); assert.equal(terminal.error?.code, 4030);
  const wrongProfile = await rpc(5, 'session.create', { profile: 'other' }); assert.equal(wrongProfile.error?.code, 4030);
  const closed = event(phone, 'close');
  await request(connection, `/api/mobile/devices/${encodeURIComponent(pair.device.id)}`, { method: 'DELETE' });
  await service.refresh('legacy');
  await deadline(closed);
  const revoked = await fetch(relay + '/api/auth/ws-ticket', { method: 'POST', headers }); assert.equal(revoked.status, 401);
  phone.close();
  console.log('PASS: real gateway + Worker + desktop controller + candidate phone adapter; sessions, WS create/history, config denied, revocation');
} finally {
  raw?.terminate(); service?.close(); if (mf) await mf.dispose();
  child.kill('SIGTERM'); await Promise.race([new Promise(resolve => child.once('exit', resolve)), new Promise(resolve => setTimeout(resolve, 3000))]);
  if (child.exitCode === null) child.kill('SIGKILL');
  await rm(dir, { recursive: true, force: true });
}
