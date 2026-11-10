import { randomBytes, randomUUID } from 'node:crypto';
import { mkdir, readFile, open, link, unlink, chmod } from 'node:fs/promises';
import { dirname } from 'node:path';
import WebSocket from 'ws';
import { allowMobileRequest } from './admission.mjs';
import { FlowSocket } from './flow-socket.mjs';

const MAX = 8 * 1024 * 1024;
const protocol = 'clover-relay-connector-v2';
const validBase64 = s => typeof s === 'string' && s.length % 4 === 0 && /^[A-Za-z0-9+/]*={0,2}$/.test(s);
const identityValid = x => x && typeof x === 'object' && /^[a-f0-9]{32}$/.test(x.id) && /^[a-f0-9]{64}$/.test(x.secret);
function relayURL(origin, allowInsecureLocalhost) {
  const u = new URL(origin);
  const local = ['127.0.0.1', '[::1]', 'localhost'].includes(u.hostname);
  if ((u.protocol !== 'https:' && !(allowInsecureLocalhost && local && u.protocol === 'http:')) || u.username || u.password || u.pathname !== '/' || u.search || u.hash) throw Error('invalid relay origin');
  return u;
}
function backendURL(origin) {
  const u = new URL(origin);
  if (u.protocol !== 'http:' || !['127.0.0.1', '[::1]'].includes(u.hostname) || u.username || u.password || u.pathname !== '/' || u.search || u.hash) throw Error('backend must be a loopback HTTP origin');
  return u;
}
export async function loadOrCreateIdentity(path) {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  try {
    const old = JSON.parse(await readFile(path, 'utf8'));
    if (!identityValid(old)) throw Error('invalid saved identity');
    await chmod(path, 0o600); return { id: old.id, secret: old.secret };
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
  const identity = { id: randomBytes(16).toString('hex'), secret: randomBytes(32).toString('hex') };
  const tmp = `${path}.${randomUUID()}.tmp`;
  try {
    const file = await open(tmp, 'wx', 0o600);
    try { await file.writeFile(JSON.stringify(identity)); await file.sync(); } finally { await file.close(); }
    try { await link(tmp, path); } catch (error) { if (error.code !== 'EEXIST') throw error; }
    // Best effort directory sync: not supported by every desktop filesystem.
    try { const dir = await open(dirname(path), 'r'); try { await dir.sync(); } finally { await dir.close(); } } catch {}
  } finally { await unlink(tmp).catch(() => {}); }
  const saved = JSON.parse(await readFile(path, 'utf8'));
  if (!identityValid(saved)) throw Error('invalid saved identity');
  return { id: saved.id, secret: saved.secret };
}
async function enrollIdentity(origin, identity, { allowInsecureLocalhost = false } = {}) {
  const base = relayURL(origin, allowInsecureLocalhost);
  if (!identityValid(identity)) throw Error('invalid identity');
  const response = await fetch(new URL('/v1/installations', base), { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ id: identity.id, secret: identity.secret }), redirect: 'error', signal: AbortSignal.timeout(10000) });
  if (response.status !== 200 && response.status !== 201) throw Error(`enrollment failed: ${response.status}`);
  const result = await response.json(); if (result.id !== identity.id) throw Error('invalid enrollment response');
  return response.status;
}
export async function ensureEnrolled(origin, identityPath, options = {}) {
  const identity = await loadOrCreateIdentity(identityPath);
  const status = await enrollIdentity(origin, identity, options);
  return { identity, status };
}
export async function openConnector({ relayOrigin, identity, backendOrigin, allowInsecureLocalhost = false }) {
  const relay = relayURL(relayOrigin, allowInsecureLocalhost); const backend = backendURL(backendOrigin);
  if (!identityValid(identity)) throw Error('invalid identity');
  relay.protocol = relay.protocol === 'https:' ? 'wss:' : 'ws:';
  relay.pathname = `/v1/installations/${identity.id}/connect`;
  const wire = new WebSocket(relay, protocol, { headers: { Authorization: `Bearer ${identity.secret}` }, maxPayload: 23000, handshakeTimeout: 10000, followRedirects: false });
  await new Promise((resolve, reject) => { wire.once('open', resolve); wire.once('error', reject); });
  if (wire.protocol !== protocol) { wire.close(); throw Error('invalid connector protocol'); }
  const ws = new FlowSocket(wire, { maxMessageBytes: 12582912, closeOnCapacity: false });
  const controls = new Map(); const channels = new Map(); const httpJobs = new Set(); let active = 0, closing = false;
  // Capacity errors never close the shared link; callers end only their own channel.
  const send = value => { ws.send(JSON.stringify(value)); if (ws.bufferedAmount) for (const local of channels.values()) if (local.readyState === WebSocket.OPEN) local.pause(); };
  const endChannel = id => { const local = channels.get(id); if (!local) return; channels.delete(id); local.terminate(); };
  const closed = () => { closing = true; for (const job of httpJobs) job.abort(); for (const reject of controls.values()) reject(Error('connector closed')); controls.clear(); for (const id of [...channels.keys()]) endChannel(id); };
  ws.addEventListener('close', closed);
  ws.addEventListener('drain', () => { for (const local of channels.values()) if (local.isPaused) local.resume(); });
  ws.addEventListener('message', async ({ data: raw }) => {
    if (typeof raw !== 'string') { ws.close(1008, 'invalid frame'); return; }
    let m; try { m = JSON.parse(raw); } catch { ws.close(1008, 'invalid frame'); return; }
    if (!m || typeof m.id !== 'string' || !/^[a-zA-Z0-9-]{1,128}$/.test(m.id)) { ws.close(1008, 'invalid frame'); return; }
    if (m.type === 'control.result') { const done = controls.get(m.id); if (done) { controls.delete(m.id); done(null, m); } return; }
    if (m.type === 'ws.open') {
      if (!/^[a-zA-Z0-9-]{1,128}$/.test(m.id) || channels.has(m.id) || channels.size >= 4 || typeof m.ticket !== 'string' || !/^[A-Za-z0-9_-]{16,256}$/.test(m.ticket) || !Array.isArray(m.protocols) || m.protocols.length !== 2 || m.protocols[0] !== 'clover-gateway-v1' || m.protocols[1] !== `clover-gateway-ticket.${m.ticket}`) { try { send({ type: 'ws.error', id: m.id }); } catch {} return; }
      const url = new URL('/api/ws', backend); url.protocol = 'ws:';
      const local = new WebSocket(url, m.protocols, { maxPayload: 10 * 1024 * 1024, handshakeTimeout: 10000, followRedirects: false }); channels.set(m.id, local);
      local.on('error', () => { if (channels.get(m.id) !== local) return; try { send({ type: 'ws.error', id: m.id }); } catch {} endChannel(m.id); });
      local.on('open', () => { if (channels.get(m.id) !== local) return; if (local.protocol !== 'clover-gateway-v1') { try { send({ type: 'ws.error', id: m.id }); } catch {} endChannel(m.id); return; } try { send({ type: 'ws.opened', id: m.id, protocol: local.protocol }); } catch { endChannel(m.id); } });
      local.on('message', (data, binary) => { if (channels.get(m.id) !== local) return; try { if (data.length > 10 * 1024 * 1024) throw Error('oversize'); send({ type: 'ws.data', id: m.id, binary, data: binary ? data.toString('base64') : data.toString('utf8') }); } catch { endChannel(m.id); } });
      local.on('close', () => { if (channels.get(m.id) !== local) return; channels.delete(m.id); try { send({ type: 'ws.close', id: m.id, code: 1000, reason: '' }); } catch {} });
      return;
    }
    if (m.type === 'ws.data' || m.type === 'ws.close') {
      const local = channels.get(m.id); if (!local) return;
      if (m.type === 'ws.close') { endChannel(m.id); return; }
      if (local.readyState !== WebSocket.OPEN || typeof m.binary !== 'boolean' || typeof m.data !== 'string' || m.data.length > (m.binary ? 1398104 : 1048576) || (m.binary && !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(m.data))) { endChannel(m.id); return; }
      const data = m.binary ? Buffer.from(m.data, 'base64') : m.data; if (Buffer.byteLength(data) > 1048576 || local.bufferedAmount > 2 * 1024 * 1024) { endChannel(m.id); return; }
      local.send(data, { binary: m.binary }, error => { if (error) endChannel(m.id); }); return;
    }
    if (m.type !== 'http.request' || active >= 8 || !allowMobileRequest(m.method, m.target) || typeof m.body !== 'string' || m.body.length > 11184812 || !validBase64(m.body)) return;
    active++; const job = new AbortController(); httpJobs.add(job);
    // Transfer ownership into one of two bounded HTTP jobs (up to 8, matching the relay's default in-flight limit), acknowledging the
    // request before its response can begin. These jobs never replay requests.
    // The event-loop turn lets FlowSocket emit the admission ACK first; both
    // ACK and subsequent response use the same ordered connector socket.
    void (async () => {
    await new Promise(resolve => setImmediate(resolve));
    try {
      if (closing || job.signal.aborted) return;
      const body = Buffer.from(m.body, 'base64'); if (body.length > MAX) throw Error('body too large');
      const authorization = m.headers?.authorization;
      if (typeof authorization !== 'string' || !/^Bearer cdt_[A-Za-z0-9_-]{16,256}$/.test(authorization)) throw Error('invalid authorization');
      const headers = { authorization };
      for (const h of ['content-type', 'accept']) if (typeof m.headers[h] === 'string' && m.headers[h].length <= 1024) headers[h] = m.headers[h];
      const init = { method: m.method, headers, redirect: 'manual', signal: AbortSignal.any([job.signal, AbortSignal.timeout(30000)]) };
      if (!['GET', 'HEAD'].includes(m.method)) init.body = body;
      const response = await fetch(new URL(m.target, backend), init);
      if (Number(response.headers.get('content-length') || 0) > MAX) throw Error('response too large');
      const reader = response.body?.getReader(); const chunks = []; let length = 0;
      if (reader) while (true) { const { value, done } = await reader.read(); if (done) break; length += value.length; if (length > MAX) { await reader.cancel(); throw Error('response too large'); } chunks.push(value); }
      const contentType = response.headers.get('content-type');
      if (ws.readyState === WebSocket.OPEN) send({ type: 'http.response', id: m.id, status: response.status, headers: contentType ? { 'content-type': contentType } : {}, body: Buffer.concat(chunks).toString('base64') });
    } catch { if (ws.readyState === WebSocket.OPEN) { try { send({ type: 'http.response', id: m.id, status: 502, headers: { 'content-type': 'application/json' }, body: Buffer.from('{"error":"backend unavailable"}').toString('base64') }); } catch {} } }
    finally { httpJobs.delete(job); active--; }
    })();
  });

  return {
    replaceDevices(devices) { return new Promise((resolve, reject) => {
      const id = randomUUID(); const timer = setTimeout(() => { controls.delete(id); reject(Error('control deadline')); }, 10000);
      controls.set(id, (error, result) => { clearTimeout(timer); if (error) reject(error); else if (!result.ok) reject(Error('device registration rejected')); else resolve(); });
      try { send({ type: 'devices.replace', id, devices }); } catch (error) { const finish = controls.get(id); controls.delete(id); finish?.(error); }
    }); },
    close() { closed(); ws.close(); },
  };
}
