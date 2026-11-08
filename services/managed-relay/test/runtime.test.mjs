import test from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes, createHash } from 'node:crypto';
import { buildSync } from 'esbuild';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Miniflare } from 'miniflare';
import { FlowSocket } from '../src/flow-socket.mjs';

const digest = x => createHash('sha256').update(x).digest('hex');
const makeIdentity = () => ({ id: randomBytes(16).toString('hex'), secret: randomBytes(32).toString('hex') });
const origin = 'https://relay.example.test';
async function start(overrides = {}, persist = false) {
  buildSync({ entryPoints: ['src/worker.mjs'], outfile: 'dist/worker.mjs', bundle: true, format: 'esm', platform: 'neutral' });
  const mf = new Miniflare({ durableObjectsPersist: persist, workers: [{ name: 'relay', modules: true, scriptPath: 'dist/worker.mjs', compatibilityDate: '2026-07-30',
    bindings: { ENROLLMENT_ENABLED: 'true', ALLOW_LEGACY_TEST_TRANSPORT: 'true', MAX_INSTALLATIONS: '10', REQUEST_TIMEOUT_MS: '300', ALLOW_TEST_TIMEOUT: 'true', MAX_ENROLLMENTS_PER_IP_DAY: '10', ...overrides },
    durableObjects: { INSTALLATIONS: { className: 'Installation', useSQLite: true }, DIRECTORY: { className: 'Directory', useSQLite: true }, GUARD: { className: 'Guard', useSQLite: true } } }] });
  try { await mf.ready; return mf; } catch (error) { await mf.dispose(); throw error; }
}
const enroll = (mf, identity) => mf.dispatchFetch(origin + '/v1/installations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(identity) });

test('real Worker runtime enrolls idempotently and rejects ownership takeover', async t => {
  const mf = await start(); t.after(() => mf.dispose());
  const a = makeIdentity(), b = makeIdentity();
  let r = await enroll(mf, a); assert.equal(r.status, 201); assert.equal((await r.json()).id, a.id);
  r = await enroll(mf, a); assert.equal(r.status, 200);
  assert.equal((await enroll(mf, { ...b, id: a.id })).status, 409);
  assert.equal((await enroll(mf, b)).status, 201);
  assert.equal((await mf.dispatchFetch(origin + '/api/config')).status, 403);
  assert.equal((await mf.dispatchFetch(origin + '/api/sessions')).status, 401);
});

test('real Worker enforces cap while permitting concurrent idempotent retries', async t => {
  const mf = await start({ MAX_INSTALLATIONS: '1' }); t.after(() => mf.dispose());
  const identity = makeIdentity();
  const responses = await Promise.all(Array.from({ length: 4 }, () => enroll(mf, identity)));
  assert.deepEqual(responses.map(r => r.status).sort(), [200, 200, 200, 201]);
  assert.equal((await enroll(mf, makeIdentity())).status, 429);
  assert.equal((await enroll(mf, identity)).status, 200);
  assert.equal((await enroll(mf, { ...makeIdentity(), id: identity.id })).status, 409);
});

test('enrollment and device mapping persist across workerd restart', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'relay-runtime-')); t.after(() => rm(dir, { recursive: true, force: true }));
  const identity = makeIdentity(), token = 'cdt_' + randomBytes(32).toString('hex');
  let mf = await start({}, dir);
  try { assert.equal((await enroll(mf, identity)).status, 201); const connector = await connect(mf, identity, token, 'before'); connector.ws.close(); }
  finally { await mf.dispose(); }
  mf = await start({}, dir); t.after(() => mf.dispose());
  assert.equal((await enroll(mf, identity)).status, 200);
  assert.equal((await mf.dispatchFetch(origin + '/api/sessions', { headers: { Authorization: `Bearer ${token}` } })).status, 503);
  const connector = await connect(mf, identity, token, 'after'); t.after(() => connector.ws.close());
  assert.equal((await (await mf.dispatchFetch(origin + '/api/sessions', { headers: { Authorization: `Bearer ${token}` } })).json()).owner, 'after');
});

test('status reads do not enroll and require the installation secret', async t => {
  const mf = await start(); t.after(() => mf.dispose());
  const identity = makeIdentity(); const url = `${origin}/v1/installations/${identity.id}/status`;
  const headers = { Authorization: `Bearer ${identity.secret}` };
  assert.equal((await mf.dispatchFetch(url, { headers })).status, 401);
  assert.equal((await enroll(mf, identity)).status, 201);
  assert.equal((await mf.dispatchFetch(url, { headers: { Authorization: `Bearer ${makeIdentity().secret}` } })).status, 401);
  assert.deepEqual(await (await mf.dispatchFetch(url, { headers })).json(), { online: false });
  const token = 'cdt_' + randomBytes(32).toString('hex');
  const connector = await connect(mf, identity, token, 'desktop'); t.after(() => connector.ws.close());
  assert.deepEqual(await (await mf.dispatchFetch(url, { headers })).json(), { online: true });
});

test('bounded enrollment rejects malformed and oversized requests', async t => {
  const mf = await start(); t.after(() => mf.dispose());
  const malformed = await mf.dispatchFetch(origin + '/v1/installations', { method: 'POST', body: '{' });
  assert.equal(malformed.status, 400);
  const oversized = await mf.dispatchFetch(origin + '/v1/installations', { method: 'POST', body: 'x'.repeat(513) });
  assert.equal(oversized.status, 413);
  await assert.rejects(mf.dispatchFetch(origin + '/v1/installations', { method: 'POST', headers: { 'content-length': 'bogus' }, body: '{}' })); // HTTP client rejects malformed framing before the Worker.
  const invalid = await enroll(mf, { id: ['a'.repeat(32)], secret: 'b'.repeat(64) });
  assert.equal(invalid.status, 400);
});

test('real Worker disables enrollment explicitly', async t => {
  const mf = await start({ ENROLLMENT_ENABLED: 'false' }); t.after(() => mf.dispose());
  assert.equal((await enroll(mf, makeIdentity())).status, 503);
});

// End-to-end fixtures below use the production connector frame protocol, not a mock Worker.
export async function connect(mf, identity, token, name, options = {}) {
  const r = await mf.dispatchFetch(`${origin}/v1/installations/${identity.id}/connect`, { headers: { Upgrade: 'websocket', Authorization: `Bearer ${identity.secret}`, 'Sec-WebSocket-Protocol': 'clover-relay-connector-v2' } });
  assert.equal(r.status, 101); r.webSocket.accept(); const ws = new FlowSocket(r.webSocket);
  const pending = new Map(); let seq = 0;
  ws.addEventListener('message', e => {
    const m = JSON.parse(e.data);
    if (m.type === 'control.result') { pending.get(m.id)?.(m); pending.delete(m.id); }
    if (m.type === 'http.request') {
      if (m.target.includes('offset=999')) return; // Deliberately offline response for deadline test.
      const isTicket = m.target === '/api/auth/ws-ticket';
      const valid = m.headers.authorization === `Bearer ${token}`;
      const body = valid ? (isTicket ? { ticket: `fixture-ticket-${identity.id}-${seq++}`, ttl_seconds: options.ttl ?? 30 } : { owner: name, headers: m.headers }) : { error: 'unauthorized' };
      ws.send(JSON.stringify({ type: 'http.response', id: m.id, status: valid ? 200 : 401, headers: { 'content-type': 'application/json' }, body: Buffer.from(JSON.stringify(body)).toString('base64') }));
    }
    if (m.type === 'ws.open' && options.wsMode !== 'timeout') ws.send(JSON.stringify(options.wsMode === 'error' ? { type: 'ws.error', id: m.id } : { type: 'ws.opened', id: m.id, protocol: options.wsMode === 'wrong' ? 'wrong' : 'clover-gateway-v1' }));
    if (m.type === 'ws.data') ws.send(JSON.stringify({ ...m, data: m.binary ? m.data : `${name}:${m.data}` }));
  });
  const control = (type, data) => new Promise((resolve, reject) => { const id = `control-${seq++}`; const timer = setTimeout(() => { pending.delete(id); reject(new Error('control timeout')); }, 2000); pending.set(id, result => { clearTimeout(timer); resolve(result); }); ws.send(JSON.stringify({ type, id, ...data })); });
  const result = await control('devices.replace', { devices: [{ hash: digest(token), expiresAt: Date.now() + 600000 }] });
  assert.equal(result.ok, true);
  return { ws, control };
}

test('two isolated connectors relay HTTP, deadlines and revocation', async t => {
  const mf = await start(); t.after(() => mf.dispose());
  const a = makeIdentity(), b = makeIdentity(); await enroll(mf, a); await enroll(mf, b);
  const ta = 'cdt_' + randomBytes(32).toString('hex'), tb = 'cdt_' + randomBytes(32).toString('hex');
  const ca = await connect(mf, a, ta, 'A'), cb = await connect(mf, b, tb, 'B');
  t.after(() => { ca.ws.close(); cb.ws.close(); });
  for (const [token, owner] of [[ta,'A'],[tb,'B']]) {
    const r = await mf.dispatchFetch(origin + '/api/sessions', { headers: { Authorization: `Bearer ${token}`, 'X-API-Key': 'MUST-NOT-FORWARD', 'X-Forwarded-For': '127.0.0.1' } });
    assert.equal(r.status, 200); const body = await r.json(); assert.equal(body.owner, owner);
    assert.equal(body.headers['x-api-key'], undefined); assert.equal(body.headers['x-forwarded-for'], undefined);
  }
  const unused = 'cdt_' + randomBytes(32).toString('hex');
  const steal = await cb.control('devices.replace', { devices: [{ hash: digest(unused), expiresAt: Date.now() + 600000 }, { hash: digest(ta), expiresAt: Date.now() + 600000 }] }); assert.equal(steal.ok, false);
  const rollback = await ca.control('devices.replace', { devices: [{ hash: digest(ta), expiresAt: Date.now() + 600000 }, { hash: digest(unused), expiresAt: Date.now() + 600000 }] }); assert.equal(rollback.ok, true);
  const stillB = await mf.dispatchFetch(origin + '/api/sessions', { headers: { Authorization: `Bearer ${tb}` } });
  assert.equal((await stillB.json()).owner, 'B');
  assert.equal((await mf.dispatchFetch(origin + '/api/sessions?offset=999', { headers: { Authorization: `Bearer ${ta}` } })).status, 504);
  await ca.control('devices.replace', { devices: [] });
  assert.equal((await mf.dispatchFetch(origin + '/api/sessions', { headers: { Authorization: `Bearer ${ta}` } })).status, 401);
});

test('connector rejects wrong secret and handles offline and reconnect', async t => {
  const mf = await start(); t.after(() => mf.dispose());
  const identity = makeIdentity(); await enroll(mf, identity);
  const wrong = makeIdentity();
  const denied = await mf.dispatchFetch(`${origin}/v1/installations/${identity.id}/connect`, { headers: { Upgrade: 'websocket', Authorization: `Bearer ${wrong.secret}`, 'Sec-WebSocket-Protocol': 'clover-relay-connector-v1' } });
  assert.equal(denied.status, 401);
  const token = 'cdt_' + randomBytes(32).toString('hex');
  const first = await connect(mf, identity, token, 'first');
  const headers = { Authorization: `Bearer ${token}` };
  const closed = new Promise(resolve => first.ws.addEventListener('close', resolve, { once: true }));
  first.ws.close(1000, 'offline'); await closed;
  assert.equal((await mf.dispatchFetch(origin + '/api/sessions', { headers })).status, 503);
  const second = await connect(mf, identity, token, 'second'); t.after(() => second.ws.close());
  const response = await mf.dispatchFetch(origin + '/api/sessions', { headers });
  assert.equal(response.status, 200); assert.equal((await response.json()).owner, 'second');
});

test('replacement fences old socket and cancels in-flight work', async t => {
  const mf = await start(); t.after(() => mf.dispose());
  const identity = makeIdentity(); await enroll(mf, identity);
  const token = 'cdt_' + randomBytes(32).toString('hex');
  const first = await connect(mf, identity, token, 'first');
  const headers = { Authorization: `Bearer ${token}` };
  const pending = mf.dispatchFetch(origin + '/api/sessions?offset=999', { headers });
  await new Promise(resolve => setTimeout(resolve, 30));
  const second = await connect(mf, identity, token, 'second'); t.after(() => { first.ws.close(); second.ws.close(); });
  assert.equal((await pending).status, 503);
  const response = await mf.dispatchFetch(origin + '/api/sessions', { headers });
  assert.equal((await response.json()).owner, 'second');
});

test('expired device stops routing', async t => {
  const mf = await start(); t.after(() => mf.dispose());
  const identity = makeIdentity(), token = 'cdt_' + randomBytes(32).toString('hex');
  await enroll(mf, identity); const connector = await connect(mf, identity, token, 'desktop'); t.after(() => connector.ws.close());
  const result = await connector.control('devices.replace', { devices: [{ hash: digest(token), expiresAt: Date.now() + 150 }] }); assert.equal(result.ok, true);
  await new Promise(resolve => setTimeout(resolve, 200));
  assert.equal((await mf.dispatchFetch(origin + '/api/sessions', { headers: { Authorization: `Bearer ${token}` } })).status, 401);
});

test('one potentially large HTTP exchange remains in flight per installation', async t => {
  const mf = await start({ MAX_HTTP_INFLIGHT: '1', HTTP_QUEUE_WAIT_MS: '50' }); t.after(() => mf.dispose());
  const identity = makeIdentity(), token = 'cdt_' + randomBytes(32).toString('hex');
  await enroll(mf, identity); const connector = await connect(mf, identity, token, 'desktop'); t.after(() => connector.ws.close());
  const headers = { Authorization: `Bearer ${token}` };
  const requests = Array.from({ length: 3 }, () => mf.dispatchFetch(origin + '/api/sessions?offset=999', { headers }));
  const statuses = (await Promise.all(requests)).map(r => r.status).sort();
  assert.deepEqual(statuses, [503, 503, 504]);
});

test('per-minute IP tracking has a hard bound and preserves known clients', async t => {
  const mf = await start({ MAX_TRACKED_IPS: '2' }); t.after(() => mf.dispose());
  const visit = ip => mf.dispatchFetch(origin + '/api/sessions', { headers: { 'CF-Connecting-IP': ip } });
  assert.equal((await visit('192.0.2.1')).status, 401);
  assert.equal((await visit('192.0.2.2')).status, 401);
  assert.equal((await visit('192.0.2.3')).status, 429);
  assert.equal((await visit('192.0.2.1')).status, 401);
});

test('daily enrollment quota and per-minute request limit are enforced', async t => {
  const mf = await start({ MAX_ENROLLMENTS_PER_IP_DAY: '1', MAX_REQUESTS_PER_IP_MINUTE: '4' }); t.after(() => mf.dispose());
  const identity = makeIdentity();
  assert.equal((await enroll(mf, identity)).status, 201);
  assert.equal((await enroll(mf, identity)).status, 200);
  assert.equal((await enroll(mf, makeIdentity())).status, 429);
  assert.equal((await enroll(mf, identity)).status, 200);
  assert.equal((await enroll(mf, identity)).status, 429);
});

test('slow request body is cut off at a bounded deadline', { timeout: 5000 }, async t => {
  const mf = await start({ BODY_TIMEOUT_MS: '120' }); t.after(() => mf.dispose());
  const identity = makeIdentity(), token = 'cdt_' + randomBytes(32).toString('hex'); await enroll(mf, identity);
  const connector = await connect(mf, identity, token, 'desktop'); t.after(() => connector.ws.close());
  const stream = new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('x')); }, cancel() {} });
  const result = await mf.dispatchFetch(origin + '/api/auth/ws-ticket', { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: stream, duplex: 'half' });
  assert.equal(result.status, 408);
});

test('malformed connector frame closes transport and leaves routing offline', { timeout: 5000 }, async t => {
  const mf = await start(); t.after(() => mf.dispose());
  const identity = makeIdentity(), token = 'cdt_' + randomBytes(32).toString('hex'); await enroll(mf, identity);
  const connector = await connect(mf, identity, token, 'desktop'); t.after(() => { try { connector.ws.close(); } catch {} });
  const closed = new Promise(resolve => connector.ws.addEventListener('close', resolve, { once: true }));
  connector.ws.send('{invalid json'); await closed;
  const response = await mf.dispatchFetch(origin + '/api/sessions', { headers: { Authorization: `Bearer ${token}` } }); assert.equal(response.status, 503);
});

test('live channel closes when device expires', { timeout: 5000 }, async t => {
  const mf = await start(); t.after(() => mf.dispose());
  const identity = makeIdentity(), token = 'cdt_' + randomBytes(32).toString('hex'); await enroll(mf, identity);
  const connector = await connect(mf, identity, token, 'desktop'); t.after(() => connector.ws.close());
  const expiresAt = Date.now() + 500;
  await connector.control('devices.replace', { devices: [{ hash: digest(token), expiresAt }] });
  const minted = await (await mf.dispatchFetch(origin + '/api/auth/ws-ticket', { method: 'POST', headers: { Authorization: `Bearer ${token}` } })).json();
  const r = await mf.dispatchFetch(origin + '/api/ws', { headers: { Upgrade: 'websocket', 'Sec-WebSocket-Protocol': `clover-gateway-v1, clover-gateway-ticket.${minted.ticket}` } }); assert.equal(r.status, 101);
  const phone = r.webSocket; phone.accept(); const closed = new Promise(resolve => phone.addEventListener('close', resolve, { once: true }));
  assert.equal((await closed).code, 1008);
});

test('ambiguous WebSocket protocols and destination hints are rejected', async t => {
  const mf = await start(); t.after(() => mf.dispose());
  const ticket = 'fixture-ticket-1234567890';
  for (const protocol of [`clover-gateway-v1, clover-gateway-ticket.${ticket}, clover-gateway-ticket.another-ticket-123456`, 'x'.repeat(513), 'clover-gateway-v1']) {
    assert.equal((await mf.dispatchFetch(origin + '/api/ws', { headers: { Upgrade: 'websocket', 'Sec-WebSocket-Protocol': protocol } })).status, 400);
  }
  assert.equal((await mf.dispatchFetch(origin + '/api/ws?destination=x', { headers: { Upgrade: 'websocket', 'Sec-WebSocket-Protocol': `clover-gateway-v1, clover-gateway-ticket.${ticket}` } })).status, 400);
  assert.equal((await mf.dispatchFetch(origin + '/api/ws', { headers: { Upgrade: 'websocket', 'X-Destination': 'x', 'Sec-WebSocket-Protocol': `clover-gateway-v1, clover-gateway-ticket.${ticket}` } })).status, 400);
});

test('oversized phone frame and connector replacement close channels', { timeout: 10000 }, async t => {
  const mf = await start(); t.after(() => mf.dispose());
  const identity = makeIdentity(), token = 'cdt_' + randomBytes(32).toString('hex'); await enroll(mf, identity);
  const first = await connect(mf, identity, token, 'first'); t.after(() => { try { first.ws.close(); } catch {} });
  const open = async () => { const minted = await (await mf.dispatchFetch(origin + '/api/auth/ws-ticket', { method: 'POST', headers: { Authorization: `Bearer ${token}` } })).json(); const r = await mf.dispatchFetch(origin + '/api/ws', { headers: { Upgrade: 'websocket', 'Sec-WebSocket-Protocol': `clover-gateway-v1, clover-gateway-ticket.${minted.ticket}` } }); assert.equal(r.status, 101); r.webSocket.accept(); return r.webSocket; };
  const large = await open(); const largeClosed = new Promise(resolve => large.addEventListener('close', resolve, { once: true })); large.send('x'.repeat(1048577)); assert.equal((await largeClosed).code, 1009);
  const phone = await open(); const closed = new Promise(resolve => phone.addEventListener('close', resolve, { once: true }));
  const second = await connect(mf, identity, token, 'second'); t.after(() => second.ws.close()); await closed;
});

test('ticket expiry, device revocation and channel cap are enforced', { timeout: 10000 }, async t => {
  const mf = await start(); t.after(() => mf.dispose());
  const identity = makeIdentity(), token = 'cdt_' + randomBytes(32).toString('hex'); await enroll(mf, identity);
  const connector = await connect(mf, identity, token, 'desktop', { ttl: 1 }); t.after(() => connector.ws.close());
  const mint = async () => (await (await mf.dispatchFetch(origin + '/api/auth/ws-ticket', { method: 'POST', headers: { Authorization: `Bearer ${token}` } })).json()).ticket;
  const headers = ticket => ({ Upgrade: 'websocket', 'Sec-WebSocket-Protocol': `clover-gateway-v1, clover-gateway-ticket.${ticket}` });
  const expired = await mint(); await new Promise(resolve => setTimeout(resolve, 1100)); assert.equal((await mf.dispatchFetch(origin + '/api/ws', { headers: headers(expired) })).status, 401);
  const phones = []; t.after(() => phones.forEach(p => { try { p.close(); } catch {} }));
  for (let i = 0; i < 4; i++) { const r = await mf.dispatchFetch(origin + '/api/ws', { headers: headers(await mint()) }); assert.equal(r.status, 101); r.webSocket.accept(); phones.push(r.webSocket); }
  assert.equal((await mf.dispatchFetch(origin + '/api/ws', { headers: headers(await mint()) })).status, 503);
  const stale = await mint(); await connector.control('devices.replace', { devices: [] });
  assert.equal((await mf.dispatchFetch(origin + '/api/ws', { headers: headers(stale) })).status, 401);
});

test('handshake failures, timeout, and invalid tickets never upgrade', { timeout: 10000 }, async t => {
  const mf = await start(); t.after(() => mf.dispose());
  const identity = makeIdentity(), token = 'cdt_' + randomBytes(32).toString('hex'); await enroll(mf, identity);
  const headersFor = ticket => ({ Upgrade: 'websocket', 'Sec-WebSocket-Protocol': `clover-gateway-v1, clover-gateway-ticket.${ticket}` });
  assert.equal((await mf.dispatchFetch(origin + '/api/ws', { headers: headersFor('unknown-ticket-123456789') })).status, 401);
  for (const mode of ['error', 'wrong', 'timeout']) {
    const connector = await connect(mf, identity, token, 'desktop', { wsMode: mode });
    const minted = await (await mf.dispatchFetch(origin + '/api/auth/ws-ticket', { method: 'POST', headers: { Authorization: `Bearer ${token}` } })).json();
    const headers = headersFor(minted.ticket); assert.notEqual((await mf.dispatchFetch(origin + '/api/ws', { headers })).status, 101);
    assert.equal((await mf.dispatchFetch(origin + '/api/ws', { headers })).status, 401); connector.ws.close();
  }
});

test('two installations route tickets independently and revoke live channels', { timeout: 10000 }, async t => {
  const mf = await start(); t.after(() => mf.dispose());
  const identities = [makeIdentity(), makeIdentity()], tokens = identities.map(() => 'cdt_' + randomBytes(32).toString('hex'));
  const connectors = []; for (let i = 0; i < 2; i++) { await enroll(mf, identities[i]); connectors.push(await connect(mf, identities[i], tokens[i], `owner${i}`)); }
  t.after(() => connectors.forEach(c => c.ws.close()));
  const phones = [];
  for (let i = 0; i < 2; i++) {
    const ticket = await (await mf.dispatchFetch(origin + '/api/auth/ws-ticket', { method: 'POST', headers: { Authorization: `Bearer ${tokens[i]}` } })).json();
    const headers = { Upgrade: 'websocket', 'Sec-WebSocket-Protocol': `clover-gateway-v1, clover-gateway-ticket.${ticket.ticket}` };
    const response = await mf.dispatchFetch(origin + '/api/ws', { headers }); assert.equal(response.status, 101); assert.equal((await mf.dispatchFetch(origin + '/api/ws', { headers })).status, 401);
    const phone = response.webSocket; phone.accept(); phones.push(phone);
    const received = new Promise(resolve => phone.addEventListener('message', e => resolve(e.data), { once: true })); phone.send('hello'); assert.equal(await received, `owner${i}:hello`);
  }
  t.after(() => phones.forEach(p => { try { p.close(); } catch {} }));
  const closed = new Promise(resolve => phones[0].addEventListener('close', resolve, { once: true }));
  assert.equal((await connectors[0].control('devices.replace', { devices: [] })).ok, true);
  await closed;
  const received = new Promise(resolve => phones[1].addEventListener('message', e => resolve(e.data), { once: true })); phones[1].send('still'); assert.equal(await received, 'owner1:still');
});

test('connector v2 negotiates bounded framing and rejects legacy transport by default', {timeout:5000}, async t => {
  const mf = await start({ALLOW_LEGACY_TEST_TRANSPORT:'false'}); t.after(() => mf.dispose());
  const identity = makeIdentity(); await enroll(mf,identity);
  const headers = {Upgrade:'websocket',Authorization:`Bearer ${identity.secret}`,'Sec-WebSocket-Protocol':'clover-relay-connector-v1'};
  const url = `${origin}/v1/installations/${identity.id}/connect`;
  assert.equal((await mf.dispatchFetch(url,{headers})).status,426);
  headers['Sec-WebSocket-Protocol']='clover-relay-connector-v2';
  const r = await mf.dispatchFetch(url,{headers}); assert.equal(r.status,101);
  assert.equal(r.headers.get('Sec-WebSocket-Protocol'),'clover-relay-connector-v2');
  r.webSocket.accept(); const ws = new FlowSocket(r.webSocket); t.after(() => ws.close());
  const result = new Promise(resolve => ws.addEventListener('message',e => resolve(JSON.parse(e.data)),{once:true}));
  ws.send(JSON.stringify({type:'devices.replace',id:'register-v2',devices:[]})); assert.equal((await result).ok,true);
});

test('managed phone requires flow negotiation and relays through bounded framing', { timeout: 5000 }, async t => {
  const mf = await start({ ALLOW_LEGACY_TEST_TRANSPORT: 'false' }); t.after(() => mf.dispose());
  const identity = makeIdentity(), token = 'cdt_' + randomBytes(32).toString('hex'); await enroll(mf, identity);
  const connector = await connect(mf, identity, token, 'bounded'); t.after(() => connector.ws.close());
  const minted = await (await mf.dispatchFetch(origin + '/api/auth/ws-ticket', {method:'POST',headers:{Authorization:`Bearer ${token}`}})).json();
  const old = { Upgrade:'websocket','Sec-WebSocket-Protocol': `clover-gateway-v1, clover-gateway-ticket.${minted.ticket}` };
  assert.equal((await mf.dispatchFetch(origin + '/api/ws', { headers: old })).status, 426);
  const headers = {...old,'Sec-WebSocket-Protocol': `clover-relay-flow-v1, clover-gateway-v1, clover-gateway-ticket.${minted.ticket}`};
  const r = await mf.dispatchFetch(origin + '/api/ws', {headers}); assert.equal(r.status,101);
  assert.equal(r.headers.get('Sec-WebSocket-Protocol'),'clover-relay-flow-v1');
  r.webSocket.accept(); const phone = new FlowSocket(r.webSocket); t.after(() => phone.close());
  const received = new Promise(resolve => phone.addEventListener('message', e => resolve(e.data), {once:true}));
  phone.send('hello'); assert.equal(await received,'bounded:hello');
});

test('PHONE_FLOW=off selects the plain protocol even when the phone offers flow', { timeout: 5000 }, async t => {
  const mf = await start({ PHONE_FLOW: 'off' }); t.after(() => mf.dispose());
  const identity = makeIdentity(), token = 'cdt_' + randomBytes(32).toString('hex'); await enroll(mf, identity);
  const connector = await connect(mf, identity, token, 'plain'); t.after(() => connector.ws.close());
  const minted = await (await mf.dispatchFetch(origin + '/api/auth/ws-ticket', {method:'POST',headers:{Authorization:`Bearer ${token}`}})).json();
  const headers = { Upgrade:'websocket','Sec-WebSocket-Protocol': `clover-relay-flow-v1, clover-gateway-v1, clover-gateway-ticket.${minted.ticket}` };
  const r = await mf.dispatchFetch(origin + '/api/ws', {headers}); assert.equal(r.status,101);
  assert.equal(r.headers.get('Sec-WebSocket-Protocol'),'clover-gateway-v1');
  const phone = r.webSocket; phone.accept(); t.after(() => phone.close());
  const received = new Promise(resolve => phone.addEventListener('message', e => resolve(e.data), {once:true}));
  phone.send('hello'); assert.equal(await received,'plain:hello');
});

test('native WebSocket ticket transport', async t => {
  const mf = await start(); t.after(() => mf.dispose());
  const a = makeIdentity(); await enroll(mf, a);
  const ta = 'cdt_' + randomBytes(32).toString('hex');
  const ca = await connect(mf, a, ta, 'A'); t.after(() => ca.ws.close());
  const ticketResponse = await mf.dispatchFetch(origin + '/api/auth/ws-ticket', { method: 'POST', headers: { Authorization: `Bearer ${ta}` } });
  assert.equal(ticketResponse.status, 200); const { ticket } = await ticketResponse.json();
  const headers = { Upgrade: 'websocket', 'Sec-WebSocket-Protocol': `clover-gateway-v1, clover-gateway-ticket.${ticket}` };
  const r = await mf.dispatchFetch(origin + '/api/ws', { headers }); assert.equal(r.status, 101, r.status === 101 ? '' : await r.text());
  assert.equal(r.headers.get('Sec-WebSocket-Protocol'), 'clover-gateway-v1');
  const phone = r.webSocket; phone.accept(); const response = new Promise(resolve => phone.addEventListener('message', e => resolve(e.data), { once: true }));
  phone.send('hello'); assert.equal(await response, 'A:hello'); phone.close();
  assert.equal((await mf.dispatchFetch(origin + '/api/ws', { headers })).status, 401);
});
