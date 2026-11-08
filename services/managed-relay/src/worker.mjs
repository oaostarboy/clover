import { allowMobileRequest } from './admission.mjs';
import { FlowSocket } from './flow-socket.mjs';
import { MemoryLedger } from './memory-ledger.mjs';

// Provisional weighted transport ceiling, not an isolate heap guarantee.
const sharedMemoryLedger = new MemoryLedger(96 * 1024 * 1024);
const reserveTransport = weight => sharedMemoryLedger.reserve(weight);
const HTTP_QUEUE_LIMIT = 64, HTTP_QUEUE_WAIT_MS = 10000;

// Keep the HTTP allocation lease until the consumer drains/cancels the body.
// A deadline cancels a stalled body rather than releasing a still-retained copy.
function leasedResponse(response, release) {
  if (!response.body) { release(); return response; }
  let reader = response.body.getReader();
  const status = response.status, headers = response.headers;
  response = null;
  let timer, ended = false, cancelling = false;
  const finish = () => { if (ended) return; ended = true; clearTimeout(timer); reader = null; release(); };
  const stream = new ReadableStream({
    start(controller) {
      timer = setTimeout(async () => {
        if (ended) return;
        cancelling = true;
        try { controller.error(Error('response deadline')); } catch {}
        try { await reader?.cancel('response deadline'); } catch {}
        finish();
      }, 30000);
    },
    async pull(controller) {
      if (ended) return;
      try {
        const result = await reader.read();
        if (ended || cancelling) return;
        if (result.done) { controller.close(); finish(); }
        else controller.enqueue(result.value);
      } catch (error) { if (!ended && !cancelling) { controller.error(error); finish(); } }
    },
    async cancel(reason) { cancelling = true; clearTimeout(timer); try { await reader?.cancel(reason); } finally { finish(); } }
  }, { highWaterMark: 0 });
  return new Response(stream, { status, headers });
}

const hex = (bytes) => [...new Uint8Array(bytes)].map(b => b.toString(16).padStart(2, '0')).join('');
const hash = async value => hex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)));
const obj = (ns, name) => ns.get(ns.idFromName(name));
const reasons = new WeakMap();
const slog = (event, data = {}) => { try { console.log(JSON.stringify({ event, ...data })); } catch {} };
const cid = id => String(id || '').slice(0, 8);
// TEMPORARY: metadata-only RPC logging (method names / error codes, never params values or text).
const rpcEvents = new Set(['message.start', 'message.complete', 'error', 'session.info']);
function rpcLog(kind, id, text, bytes) {
  try {
    const ch = cid(id); let msg = null;
    if (typeof text === 'string') { try { msg = JSON.parse(text); } catch {} }
    if (!msg || typeof msg !== 'object') { slog(kind === 'rpc_up' ? 'rpc_up_raw' : 'rpc_down_raw', { ch, bytes }); return; }
    if (kind === 'rpc_up') { slog('rpc_up', { ch, id: String(msg.id).slice(0, 40), method: String(msg.method).slice(0, 60), paramKeys: Object.keys(msg.params || {}).slice(0, 12) }); return; }
    if (msg.id !== undefined && msg.method === undefined && ('result' in msg || 'error' in msg)) { slog('rpc_down', { ch, id: String(msg.id).slice(0, 40), ok: !msg.error, errCode: msg.error?.code, errMsg: String(msg.error?.message || '').slice(0, 120) }); return; }
    const t = String(msg.params?.type ?? msg.type ?? '').slice(0, 60);
    if (t && (rpcEvents.has(t) || t.includes('error'))) slog('rpc_event', { ch, type: t, method: msg.method === undefined ? undefined : String(msg.method).slice(0, 60) });
  } catch {}
}
const redactProtocols = header => String(header || '').split(',').map(x => x.trim()).map(x => x.startsWith('clover-gateway-ticket.') ? 'clover-gateway-ticket.<redacted>' : x.slice(0, 64)).slice(0, 8);
const reqFacts = request => ({ ua: (request.headers.get('user-agent') || '').slice(0, 120), protoCount: (request.headers.get('sec-websocket-protocol') || '').split(',').filter(Boolean).length, protocols: redactProtocols(request.headers.get('sec-websocket-protocol')), hasCookie: request.headers.has('cookie'), hasOrigin: request.headers.has('origin'), hasAuthorization: request.headers.has('authorization'), origin: (request.headers.get('origin') || '').slice(0, 200), cfRay: request.headers.get('cf-ray') || '' });
const reply = (value, status = 200) => Response.json(value, { status, headers: { 'Cache-Control': 'no-store' } });
let bodyReads = 0;
async function readBytes(request, limit, timeout = 10000, onCancel = () => {}) {
  if (!request.body) return new Uint8Array();
  if (bodyReads >= 32) throw Error('body busy');
  bodyReads++;
  const reader = request.body.getReader(); const chunks = []; let length = 0, timer, cancellation;
  const cancel = () => {
    if (!cancellation) { cancellation = Promise.resolve(reader.cancel()).catch(() => {}); onCancel(cancellation); }
  };
  try {
    const deadline = new Promise((_, reject) => { timer = setTimeout(() => reject(Error('body deadline')), timeout); });
    while (true) { const { value, done } = await Promise.race([reader.read(), deadline]); if (done) break; length += value.byteLength; if (length > limit) { cancel(); return null; } chunks.push(value); }
    const result = new Uint8Array(length); let offset = 0;
    for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
    return result;
  } catch (error) { cancel(); throw error; }
  finally { clearTimeout(timer); if (cancellation) void cancellation.finally(() => { bodyReads--; }); else bodyReads--; }
}
function base64(bytes) { let result = ''; for (let i = 0; i < bytes.length; i += 8192) result += String.fromCharCode(...bytes.subarray(i, i + 8192)); return btoa(result); }
async function readSmallBody(request, limit, timeout) {
  const bytes = await readBytes(request, limit, timeout);
  return bytes === null ? null : new TextDecoder('utf-8', { fatal: true }).decode(bytes);
}
const stableProtocol = 'clover-gateway-v1';
const phoneFlowProtocol = 'clover-relay-flow-v1';
const ticketPrefix = 'clover-gateway-ticket.';
const wsLimit = 1024 * 1024;
const FORWARD_QUEUE_BYTES = 8 * 1024 * 1024;
const validTicket = ticket => typeof ticket === 'string' && /^[A-Za-z0-9_-]{16,256}$/.test(ticket);
const validId = id => typeof id === 'string' && /^[a-zA-Z0-9-]{1,128}$/.test(id);
const validBase64 = s => typeof s === 'string' && s.length % 4 === 0 && /^[A-Za-z0-9+/]*={0,2}$/.test(s);
const boundedNumber = (value, fallback, max) => {
  const n = Number(value);
  return Number.isSafeInteger(n) && n > 0 ? Math.min(n, max) : fallback;
};

// Every durable object endpoint is internal: the public router constructs requests.
export class Installation {
  constructor(state, env) { this.state = state; this.env = env; this.socket = null; this.epoch = 0; this.pending = new Map(); this.channels = new Map(); this.controls = Promise.resolve(); this.controlCount = 0; this.httpInFlight = 0; this.httpWaiters = []; }
  get httpBusy() { return this.httpInFlight > 0; }
  acquireHttpSlot() {
    const limit = boundedNumber(this.env.MAX_HTTP_INFLIGHT, 8, 16);
    if (this.httpInFlight < limit) { this.httpInFlight++; return Promise.resolve('ok'); }
    if (this.httpWaiters.length >= HTTP_QUEUE_LIMIT) return Promise.resolve('busy');
    const waitMs = this.env.ALLOW_TEST_TIMEOUT === 'true' ? boundedNumber(this.env.HTTP_QUEUE_WAIT_MS, HTTP_QUEUE_WAIT_MS, HTTP_QUEUE_WAIT_MS) : HTTP_QUEUE_WAIT_MS;
    return new Promise(resolve => {
      const w = { resolve: r => { clearTimeout(w.timer); resolve(r); } };
      w.timer = setTimeout(() => { const i = this.httpWaiters.indexOf(w); if (i >= 0) this.httpWaiters.splice(i, 1); resolve('busy'); }, waitMs);
      this.httpWaiters.push(w);
    });
  }
  releaseHttpSlot() {
    const next = this.httpWaiters.shift();
    if (next) next.resolve('ok'); else this.httpInFlight = Math.max(0, this.httpInFlight - 1);
  }
  endPending() { for (const w of this.httpWaiters.splice(0)) { this.httpInFlight++; w.resolve('offline'); } for (const resolve of this.pending.values()) resolve(reply({ error: 'offline' }, 503)); this.pending.clear(); for (const id of [...this.channels.keys()]) this.endChannel(id, 1012, 'connector offline'); }
  endChannel(id, code = 1000, reason = '') {
    slog('channel_end', { ch: cid(id), code, reason });
    const channel = this.channels.get(id); if (!channel) return;
    this.channels.delete(id); clearTimeout(channel.timer); clearTimeout(channel.expiryTimer);
    for (const done of channel.waiters || []) done();
    if (channel.socket === this.socket && channel.epoch === this.epoch) { try { channel.socket.send(JSON.stringify({ type: 'ws.close', id, code: 1000, reason: '' })); } catch {} }
    if (channel.finish) { const finish = channel.finish; channel.finish = null; finish(reply({ error: 'unavailable' }, 503)); }
    if (channel.phone) { try { channel.phone.close(code, reason); } catch {} }
  }
  async openPhone(m, channel, epoch, server) {
            const identity = await this.state.storage.get('identity');
            if (epoch !== this.epoch || this.channels.get(m.id) !== channel || !identity?.devices.some(d => d.hash === channel.deviceHash && d.expiresAt > Date.now())) { this.endChannel(m.id, 1008, 'device revoked'); return; }
            const [client, rawPhone] = Object.values(new WebSocketPair()); rawPhone.accept(); rawPhone.binaryType = 'arraybuffer';
            const phone = channel.flow ? new FlowSocket(rawPhone, { reserve: reserveTransport }) : rawPhone; channel.phone = phone;
            phone.addEventListener('message', async e => {
              if (this.channels.get(m.id) !== channel || epoch !== this.epoch) return;
              if (e.data instanceof Blob && e.data.size > wsLimit) { this.endChannel(m.id, 1009, 'frame too large'); return; }
              const binary = typeof e.data !== 'string'; const bytes = binary ? new Uint8Array(e.data instanceof Blob ? await e.data.arrayBuffer() : e.data) : new TextEncoder().encode(e.data);
              if (this.channels.get(m.id) !== channel || epoch !== this.epoch) return;
              if (bytes.length > wsLimit) { this.endChannel(m.id, 1009, 'frame too large'); return; }
              rpcLog('rpc_up', m.id, binary ? null : e.data, bytes.length);
              const forward = async () => {
                const full = () => server.queue && server.queue.length >= server.queueCount;
                while (full()) {
                  // One shared drain/close listener per link; channels park a wake fn that endChannel also calls.
                  await new Promise(resolve => {
                    if (!server.linkWaiters) {
                      const set = server.linkWaiters = new Set();
                      const wakeAll = () => { for (const w of [...set]) w(); };
                      server.addEventListener('drain', wakeAll); server.addEventListener('close', wakeAll);
                    }
                    channel.waiters ||= new Set();
                    const done = () => { channel.waiters.delete(done); server.linkWaiters.delete(done); resolve(); };
                    channel.waiters.add(done); server.linkWaiters.add(done);
                    if (!full()) done();
                  });
                  if (this.channels.get(m.id) !== channel || epoch !== this.epoch || server.closed || server.readyState > 1) return;
                }
                server.send(JSON.stringify({ type: 'ws.data', id: m.id, binary, data: binary ? base64(bytes) : e.data }));
              };
              const cost = bytes.length + 256;
              channel.pendingFwdBytes = (channel.pendingFwdBytes || 0) + cost;
              if (channel.pendingFwdBytes > FORWARD_QUEUE_BYTES) { this.endChannel(m.id, 1008, 'too many pending'); return; }
              channel.fwd = (channel.fwd || Promise.resolve()).then(forward).catch(() => this.endChannel(m.id, 1011, 'transport error')).finally(() => { channel.pendingFwdBytes -= cost; });
            });
            const closePhone = e => { slog('phone_socket_close', { ch: cid(m.id), type: e?.type, code: e?.code, reason: String(e?.reason || '').slice(0, 80) }); if (this.channels.get(m.id) !== channel) return; try { server.send(JSON.stringify({ type: 'ws.close', id: m.id, code: 1000, reason: '' })); } catch {} this.endChannel(m.id); };
            phone.addEventListener('close', closePhone); phone.addEventListener('error', closePhone);
            slog('channel_opened_101', { ch: cid(m.id) }); clearTimeout(channel.timer); const finish = channel.finish; channel.finish = null; finish(new Response(null, { status: 101, webSocket: client, headers: { 'Sec-WebSocket-Protocol': channel.flow ? phoneFlowProtocol : stableProtocol } }));
  }
  revokeChannels(devices) {
    const now = Date.now();
    for (const [id, channel] of this.channels) {
      const device = devices.find(d => d.hash === channel.deviceHash && d.expiresAt > now);
      if (!device) { try { this.socket?.send(JSON.stringify({ type: 'ws.close', id, code: 1008, reason: 'device revoked' })); } catch {} this.endChannel(id, 1008, 'device revoked'); }
      else { clearTimeout(channel.expiryTimer); channel.expiryTimer = setTimeout(() => { try { this.socket?.send(JSON.stringify({ type: 'ws.close', id, code: 1008, reason: 'device expired' })); } catch {} this.endChannel(id, 1008, 'device expired'); }, Math.min(device.expiresAt - now, 2147483647)); }
    }
  }
  async replaceDevices(message, epoch) {
    const identity = await this.state.storage.get('identity');
    const devices = message.devices;
    if (!Array.isArray(devices) || devices.length > 64 || devices.some(d => !d || typeof d.hash !== 'string' || !/^[a-f0-9]{64}$/.test(d.hash) || !Number.isSafeInteger(d.expiresAt) || d.expiresAt <= Date.now() || d.expiresAt > Date.now() + 90 * 86400000) || new Set(devices.map(d => d.hash)).size !== devices.length) throw Error('invalid devices');
    const old = new Map(identity.devices.map(d => [d.hash, d]));
    const claimed = []; const revision = crypto.randomUUID();
    try {
      for (const device of devices) {
        if (epoch !== this.epoch) throw Error('stale connector');
        const r = await obj(this.env.DIRECTORY, device.hash).fetch('https://internal/claim', { method: 'POST', body: JSON.stringify({ id: identity.id, expiresAt: device.expiresAt, revision }) });
        if (!r.ok) throw Error('device unavailable');
        claimed.push(device.hash);
      }
      await this.state.blockConcurrencyWhile(async () => {
        if (epoch !== this.epoch) throw Error('stale connector');
        await this.state.storage.put('identity', { ...identity, devices });
      });
    } catch (error) {
      for (const key of claimed) {
        const previous = old.get(key);
        await obj(this.env.DIRECTORY, key).fetch('https://internal/rollback', { method: 'POST', body: JSON.stringify({ id: identity.id, revision, previous: previous ? { id: identity.id, expiresAt: previous.expiresAt } : null }) });
      }
      throw error;
    }
    this.revokeChannels(devices);
    const keep = new Set(devices.map(d => d.hash));
    for (const d of identity.devices) if (!keep.has(d.hash)) await obj(this.env.DIRECTORY, d.hash).fetch('https://internal/remove', { method: 'POST', body: JSON.stringify({ id: identity.id }) });
  }
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method === 'POST' && url.pathname === '/initialize') {
      const { id, secretHash } = await request.json();
      return this.state.blockConcurrencyWhile(async () => {
        const existing = await this.state.storage.get('identity');
        if (existing) return reply({ id }, existing.secretHash === secretHash ? 200 : 409);
        await this.state.storage.put('identity', { id, secretHash, devices: [] });
        return reply({ id }, 201);
      });
    }
    if (url.pathname === '/status' && request.method === 'GET') {
      const identity = await this.state.storage.get('identity');
      const match = /^Bearer ([a-f0-9]{64})$/.exec(request.headers.get('authorization') || '');
      if (!identity || !match || await hash(match[1]) !== identity.secretHash) return reply({ error: 'unauthorized' }, 401);
      return reply({ online: !!this.socket });
    }
    if (url.pathname === '/connect' && request.method === 'GET') {
      const identity = await this.state.storage.get('identity');
      const supplied = request.headers.get('authorization') || '';
      const match = /^Bearer ([a-f0-9]{64})$/.exec(supplied);
      if (!identity || !match || await hash(match[1]) !== identity.secretHash) return reply({ error: 'unauthorized' }, 401);
      const offered = (request.headers.get('sec-websocket-protocol') || '').split(',').map(s => s.trim());
      const flow = offered.includes('clover-relay-connector-v2');
      if (request.headers.get('upgrade')?.toLowerCase() !== 'websocket' || (!flow && !offered.includes('clover-relay-connector-v1'))) return reply({ error: 'invalid upgrade' }, 400);
      if (!flow && this.env.ALLOW_LEGACY_TEST_TRANSPORT !== 'true') return reply({ error: 'desktop update required' }, 426);
      const [client, rawServer] = Object.values(new WebSocketPair()); rawServer.accept();
      const server = flow ? new FlowSocket(rawServer, { maxMessageBytes: 12582912, reserve: reserveTransport }) : rawServer;
      const epoch = ++this.epoch;
      const previous = this.socket; this.socket = server; this.endPending();
      if (previous) { try { previous.close(1012, 'replaced'); } catch {} }

      server.addEventListener('message', async event => {
        if (epoch !== this.epoch) return;
        if (typeof event.data !== 'string' || event.data.length > 12 * 1024 * 1024) { try { server.close(1008, 'invalid frame'); } catch {} return; }
        let m; try { m = JSON.parse(event.data); } catch { try { server.close(1008, 'invalid frame'); } catch {} return; }
        if (!m || typeof m !== 'object' || !validId(m.id)) { try { server.close(1008, 'invalid frame'); } catch {} return; }
        if (m.type === 'devices.replace' && m.id.length <= 128) {
          if (this.controlCount >= 4) { try { server.close(1008, 'control limit'); } catch {} return; }
          this.controlCount++;
          this.controls = this.controls.then(async () => {
            try { await this.replaceDevices(m, epoch); if (epoch === this.epoch) server.send(JSON.stringify({ type: 'control.result', id: m.id, ok: true })); }
            catch { if (epoch === this.epoch) server.send(JSON.stringify({ type: 'control.result', id: m.id, ok: false, error: 'invalid or unavailable devices' })); }
            finally { this.controlCount--; }
          }).catch(() => {});
        }
        if (m.type === 'ws.opened' || m.type === 'ws.error' || m.type === 'ws.data' || m.type === 'ws.close') {
          const channel = this.channels.get(m.id); if (!channel || channel.epoch !== epoch) return;
          if (m.type === 'ws.opened') {
            if (!channel.finish || m.protocol !== stableProtocol) { this.endChannel(m.id, 1008, 'invalid protocol'); return; }
            // ws.data from the connector can arrive while we await storage; make it wait for the phone socket.
            let opened; channel.opening = new Promise(r => { opened = r; });
            try { await this.openPhone(m, channel, epoch, server); } finally { channel.opening = null; opened(); }
            return;
          }
          if (channel.opening && m.type !== 'ws.error' && m.type !== 'ws.close') { await channel.opening; if (this.channels.get(m.id) !== channel || channel.epoch !== this.epoch) return; }
          if (m.type === 'ws.error') { slog('connector_ws_error', { ch: cid(m.id) }); this.endChannel(m.id, 1011, 'backend unavailable'); }
          else if (m.type === 'ws.close') this.endChannel(m.id, 1000, '');
          else {
            if (!channel.phone || typeof m.binary !== 'boolean' || typeof m.data !== 'string' || m.data.length > (m.binary ? 1398104 : wsLimit) || (m.binary && !validBase64(m.data))) { this.endChannel(m.id, 1008, 'invalid frame'); return; }
            try {
              const data = m.binary ? Uint8Array.from(atob(m.data), c => c.charCodeAt(0)) : m.data;
              if ((m.binary ? data.byteLength : new TextEncoder().encode(data).length) > wsLimit) throw Error('oversize');
              const phone = channel.phone;
              if (!m.binary) rpcLog('rpc_down', m.id, data, data.length);
              phone.send(m.binary ? data.buffer : data);
              // Hold the connector grant until the phone has consumed this message.
              // This propagates backpressure instead of filling the phone queue.
              if (channel.flow && phone.bufferedAmount) await new Promise(resolve => {
                channel.waiters ||= new Set();
                const done = () => { channel.waiters.delete(done); phone.removeEventListener('drain', done); phone.removeEventListener('close', done); resolve(); };
                channel.waiters.add(done);
                phone.addEventListener('drain', done); phone.addEventListener('close', done);
                if (this.channels.get(m.id) !== channel || epoch !== this.epoch || phone.readyState !== 1 || !phone.bufferedAmount) done();
              });
            } catch { this.endChannel(m.id, 1011, 'transport error'); }
          }
          return;
        }
        if (m.type === 'http.response') {
          const resolve = this.pending.get(m.id);
          if (!resolve || resolve.processing) return;
          resolve.processing = true;
          let response;
          try {
            if (!Number.isInteger(m.status) || m.status < 200 || m.status > 599 || typeof m.body !== 'string' || m.body.length > 11184812 || !validBase64(m.body)) throw Error('bad response');
            const binary = atob(m.body); if (binary.length > 8 * 1024 * 1024) throw Error('oversize');
            const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
            const headers = new Headers();
            const contentType = m.headers?.['content-type'];
            if (typeof contentType === 'string' && /^(application\/(json|octet-stream|pdf)|text\/plain|audio\/[a-z0-9.+-]+|image\/(png|jpeg|webp))(?:\s*;[^\r\n]*)?$/i.test(contentType)) headers.set('content-type', contentType);
            headers.set('cache-control', 'no-store');
            if (resolve.ticketRequest && m.status === 200) {
              if (bytes.length > 4096 || !/^application\/json(?:\s*;|$)/i.test(contentType || '')) throw Error('invalid ticket response');
              const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
              if (!value || !validTicket(value.ticket) || !Number.isSafeInteger(value.ttl_seconds) || value.ttl_seconds < 1 || value.ttl_seconds > 30) throw Error('invalid ticket response');
              const expiresAt = resolve.started + value.ttl_seconds * 1000;
              if (expiresAt <= Date.now() || epoch !== this.epoch || this.pending.get(m.id) !== resolve) throw Error('expired response');
              const directory = obj(this.env.DIRECTORY, `ticket:${await hash(value.ticket)}`);
              const claimId = crypto.randomUUID();
              const claimed = await directory.fetch('https://internal/ticket-claim', { method: 'POST', body: JSON.stringify({ id: resolve.installationId, deviceHash: resolve.deviceHash, expiresAt, claimId }) });
              if (!claimed.ok) throw Error('ticket unavailable');
              if (epoch !== this.epoch || this.pending.get(m.id) !== resolve) { await directory.fetch('https://internal/ticket-cancel', { method: 'POST', body: JSON.stringify({ claimId }) }); throw Error('ticket unavailable'); }
            }
            response = new Response([204,205,304].includes(m.status) ? null : bytes, { status: m.status, headers });
          } catch { response = reply({ error: 'invalid response' }, 502); }
          this.pending.delete(m.id);
          try { await resolve(response, true); } finally { resolve.processingDone(); }
        }
      });
      const close = () => { if (this.socket === server && this.epoch === epoch) { this.socket = null; this.endPending(); } };
      server.addEventListener('close', close); server.addEventListener('error', close);
      return new Response(null, { status: 101, webSocket: client, headers: { 'Sec-WebSocket-Protocol': flow ? 'clover-relay-connector-v2' : 'clover-relay-connector-v1' } });
    }
    if (url.pathname === '/open-ws' && request.method === 'GET') {
      const ticket = request.headers.get('x-relay-ticket'), deviceHash = request.headers.get('x-relay-device-hash'); const identity = await this.state.storage.get('identity');
      const device = identity?.devices.find(d => d.hash === deviceHash && d.expiresAt > Date.now());
      if (!device || !validTicket(ticket)) { slog('open_ws_reject', { reason: 'open_ws_unauthorized' }); return reply({ error: 'unauthorized' }, 401); }
      if (!this.socket) { slog('open_ws_reject', { reason: 'open_ws_offline' }); return reply({ error: 'offline' }, 503); }
      if (this.channels.size >= 4) { slog('open_ws_reject', { reason: 'open_ws_busy' }); return reply({ error: 'busy' }, 503); }
      const id = crypto.randomUUID(), socket = this.socket, epoch = this.epoch;
      return await new Promise(resolve => {
        const channel = { deviceHash, epoch, socket, finish: resolve, phone: null, flow: request.headers.get('x-relay-phone-flow') === 'v1' };
        this.channels.set(id, channel);
        slog('channel_open_requested', { ch: cid(id), flow: channel.flow });
        const configured = boundedNumber(this.env.REQUEST_TIMEOUT_MS, 15000, 120000);
        const timeout = this.env.ALLOW_TEST_TIMEOUT === 'true' ? Math.max(100, configured) : Math.max(5000, configured);
        channel.timer = setTimeout(() => this.endChannel(id, 1011, 'deadline exceeded'), timeout);
        this.revokeChannels(identity.devices);
        try { socket.send(JSON.stringify({ type: 'ws.open', id, ticket, protocols: [stableProtocol, ticketPrefix + ticket] })); }
        catch { this.endChannel(id, 1011, 'offline'); }
      });
    }
    if (url.pathname === '/forward' && request.method === 'POST') {
      const deviceHash = request.headers.get('x-relay-device-hash');
      const method = request.headers.get('x-relay-method'), target = request.headers.get('x-relay-target');
      const identity = await this.state.storage.get('identity');
      if (!identity?.devices.some(d => d.hash === deviceHash && d.expiresAt > Date.now())) return reply({ error: 'unauthorized' }, 401);
      if (!allowMobileRequest(method, target)) return reply({ error: 'forbidden' }, 403);
      if (!this.socket) return reply({ error: 'offline' }, 503);
      const slot = await this.acquireHttpSlot();
      if (slot !== 'ok') { if (slot === 'offline') this.releaseHttpSlot(); return reply({ error: slot }, 503); }
      if (!this.socket) { this.releaseHttpSlot(); return reply({ error: 'offline' }, 503); }
      const leaseBytes = boundedNumber(this.env.HTTP_EXCHANGE_MB, 8, 60) * 1024 * 1024;
      const leaseWaitMs = this.env.ALLOW_TEST_TIMEOUT === 'true' ? boundedNumber(this.env.HTTP_QUEUE_WAIT_MS, HTTP_QUEUE_WAIT_MS, HTTP_QUEUE_WAIT_MS) : HTTP_QUEUE_WAIT_MS;
      // Memory pressure queues like a full slot table: retry until a lease frees up, within the same wait cap.
      let lease = sharedMemoryLedger.reserve(leaseBytes);
      for (const until = Date.now() + leaseWaitMs; !lease && Date.now() < until && this.socket;) { await new Promise(r => setTimeout(r, 25)); lease = sharedMemoryLedger.reserve(leaseBytes); }
      if (!lease) { this.releaseHttpSlot(); return reply({ error: this.socket ? 'busy' : 'offline' }, 503); }
      if (!this.socket) { lease(); this.releaseHttpSlot(); return reply({ error: 'offline' }, 503); }
      let released = false;
      const release = () => { if (released) return; released = true; this.releaseHttpSlot(); lease(); };
      try {
      const id = crypto.randomUUID(), socket = this.socket, epoch = this.epoch;
      let body, cancelling = false;
      try { body = await readBytes(request, 8 * 1024 * 1024, this.env.ALLOW_TEST_TIMEOUT === 'true' ? boundedNumber(this.env.BODY_TIMEOUT_MS, 10000, 10000) : 10000, settled => { cancelling = true; void settled.finally(release); }); }
      catch { if (!cancelling) release(); return reply({ error: 'body deadline' }, 408); }
      if (!body) { if (!cancelling) release(); return reply({ error: 'too large' }, 413); }
      const current = await this.state.storage.get('identity');
      if (epoch !== this.epoch || socket !== this.socket || !current?.devices.some(d => d.hash === deviceHash && d.expiresAt > Date.now())) { body = null; release(); return reply({ error: 'unavailable' }, 503); }
      const headers = {};
      for (const name of ['authorization','content-type','accept']) { const value = request.headers.get(name); if (value) headers[name] = value; }
      const configured = boundedNumber(this.env.REQUEST_TIMEOUT_MS, 15000, 120000);
      const timeout = this.env.ALLOW_TEST_TIMEOUT === 'true' ? Math.max(100, configured) : Math.max(5000, configured);
      return await new Promise(resolve => {
        let timer, finished = false, deferredRelease = false;
        const finish = (response, fromBackend = false) => {
          if (finished) return response.body?.cancel().catch(() => {});
          finished = true; clearTimeout(timer); this.pending.delete(id);
          if (fromBackend) resolve(leasedResponse(response, release));
          else { if (finish.processing) deferredRelease = true; else release(); resolve(response); }
        };
        finish.processingDone = () => { finish.processing = false; if (deferredRelease) release(); };
        Object.assign(finish, { ticketRequest: method === 'POST' && target === '/api/auth/ws-ticket', started: Number(request.headers.get('x-relay-started')), deviceHash, installationId: identity.id });
        this.pending.set(id, finish); timer = setTimeout(() => finish(reply({ error: 'deadline exceeded' }, 504)), timeout);
        try { socket.send(JSON.stringify({ type: 'http.request', id, method, target, headers, body: base64(body) })); }
        catch { finish(reply({ error: 'offline' }, 503)); }
        finally { body = null; }
      });
      } catch (error) { release(); throw error; }
    }
    return reply({ error: 'unavailable' }, 503);
  }
}

export class Directory {
  constructor(state, env) { this.state = state; this.env = env; }
  async alarm() { const current = await this.state.storage.get('owner'); if (current?.deviceHash && current.expiresAt <= Date.now()) await this.state.storage.delete('owner'); }
  async fetch(request) {
    const path = new URL(request.url).pathname;
    return this.state.blockConcurrencyWhile(async () => {
      const current = await this.state.storage.get('owner');
      if (path === '/ticket-consume' && request.method === 'POST') {
        if (!current || current.expiresAt <= Date.now() || !current.deviceHash) { if (current) await this.state.storage.delete('owner'); return reply({ error: 'unauthorized' }, 401); }
        await this.state.storage.delete('owner'); return reply({ id: current.id, deviceHash: current.deviceHash });
      }
      if (path === '/lookup') return current && current.expiresAt > Date.now() ? reply({ id: current.id }) : reply({ error: 'unauthorized' }, 401);
      if (request.method !== 'POST') return reply({ error: 'invalid' }, 400);
      const data = await request.json();
      if (path === '/ticket-cancel') { if (current?.claimId === data.claimId) await this.state.storage.delete('owner'); return reply({ ok: true }); }
      if (path === '/ticket-claim') {
        if (current && current.expiresAt > Date.now()) return reply({ error: 'conflict' }, 409);
        if (!/^[a-f0-9]{32}$/.test(data.id) || !/^[a-f0-9]{64}$/.test(data.deviceHash) || !Number.isSafeInteger(data.expiresAt) || data.expiresAt <= Date.now()) return reply({ error: 'invalid' }, 400);
        await this.state.storage.put('owner', { id: data.id, deviceHash: data.deviceHash, expiresAt: data.expiresAt, claimId: data.claimId }); await this.state.storage.setAlarm(data.expiresAt); return reply({ ok: true });
      }
      if (path === '/rollback') {
        if (current?.id === data.id && current.revision === data.revision) { if (data.previous) await this.state.storage.put('owner', data.previous); else await this.state.storage.delete('owner'); }
        return reply({ ok: true });
      }
      if (path === '/claim') {
        if (current && current.expiresAt > Date.now() && current.id !== data.id) return reply({ error: 'conflict' }, 409);
        await this.state.storage.put('owner', { id: data.id, expiresAt: data.expiresAt, revision: data.revision }); return reply({ ok: true });
      }
      if (path === '/remove') { if (current?.id === data.id) await this.state.storage.delete('owner'); return reply({ ok: true }); }
      return reply({ error: 'invalid' }, 400);
    });
  }
}

// A single guard serializes durable allocation and time-bucketed quotas.
export class Guard {
  constructor(state, env) { this.state = state; this.env = env; }
  async fetch(request) {
    const input = await request.json();
    return this.state.blockConcurrencyWhile(async () => {
      const { id, secretHash, ip } = input;
      if (new URL(request.url).pathname === '/rate') {
        const bucket = Math.floor(Date.now() / 60000);
        const old = await this.state.storage.get('rate-bucket');
        const counts = old?.bucket === bucket ? old.counts : {};
        const count = counts[ip] || 0;
        if (!count && Object.keys(counts).length >= boundedNumber(this.env.MAX_TRACKED_IPS, 1024, 1024)) return reply({ error: 'rate limited' }, 429);
        if (count >= boundedNumber(this.env.MAX_REQUESTS_PER_IP_MINUTE, 120, 100000)) return reply({ error: 'rate limited' }, 429);
        counts[ip] = count + 1;
        await this.state.storage.put('rate-bucket', { bucket, counts });
        return reply({ ok: true });
      }
      const allocation = await this.state.storage.get(`installation:${id}`);
      if (allocation && allocation !== secretHash) return reply({ error: 'conflict' }, 409);
      if (!allocation) {
        const max = boundedNumber(this.env.MAX_INSTALLATIONS, 1000, 1000000);
        const count = (await this.state.storage.get('count')) || 0;
        if (count >= max) return reply({ error: 'enrollment unavailable' }, 429);
        const day = Math.floor(Date.now() / 86400000);
        const key = `daily:${ip}`;
        const previous = await this.state.storage.get(key);
        const daily = previous?.day === day ? previous.count : 0;
        if (daily >= boundedNumber(this.env.MAX_ENROLLMENTS_PER_IP_DAY, 10, 10000)) return reply({ error: 'enrollment unavailable' }, 429);
        // Reserve verifier and both quotas atomically before any cross-object call.
        await this.state.storage.put({ [`installation:${id}`]: secretHash, count: count + 1, [key]: { day, count: daily + 1 } });
      }
      const result = await obj(this.env.INSTALLATIONS, id).fetch('https://internal/initialize', {
        method: 'POST', body: JSON.stringify({ id, secretHash })
      });
      if (result.status !== 201 && result.status !== 200) return reply({ error: 'unavailable' }, 503);
      return reply({ id }, allocation ? 200 : 201);
    });
  }
}

export default {
  async fetch(request, env) {
    const response = await this.handle(request, env);
    try {
      const path = new URL(request.url).pathname;
      if (path === '/api/ws' || path === '/api/auth/ws-ticket' || (path.startsWith('/api/') && response.status >= 400)) slog('api_request', { path, method: request.method, status: response.status, reason: reasons.get(request), ...reqFacts(request) });
    } catch {}
    return response;
  },
  async handle(request, env) {
    const R = (value, status, reason) => { reasons.set(request, reason); return reply(value, status); };
    try {
      const started = Date.now();
      const url = new URL(request.url);
      const ip = await hash(request.headers.get('CF-Connecting-IP') || 'unknown');
      const allowed = await obj(env.GUARD, 'global').fetch('https://internal/rate', { method: 'POST', body: JSON.stringify({ ip }) });
      if (!allowed.ok) return reply({ error: 'rate limited' }, 429);
      const declaredLength = request.headers.get('content-length');
      if (declaredLength !== null && !/^(?:0|[1-9][0-9]{0,15})$/.test(declaredLength)) return reply({ error: 'invalid request' }, 400);
      if (url.pathname === '/v1/installations' && request.method === 'POST') {
        if (env.ENROLLMENT_ENABLED !== 'true') return reply({ error: 'enrollment unavailable' }, 503);
        const size = Number(request.headers.get('content-length') || 0);
        if (size > 512) return reply({ error: 'invalid request' }, 413);
        const body = await readSmallBody(request, 512);
        if (body === null) return reply({ error: 'invalid request' }, 413);
        let value;
        try { value = JSON.parse(body); } catch { return reply({ error: 'invalid request' }, 400); }
        if (!value || typeof value !== 'object' || Object.keys(value).sort().join(',') !== 'id,secret' || typeof value.id !== 'string' || typeof value.secret !== 'string' || !/^[a-f0-9]{32}$/.test(value.id) || !/^[a-f0-9]{64}$/.test(value.secret)) return reply({ error: 'invalid request' }, 400);
        return obj(env.GUARD, 'global').fetch('https://internal/enroll', { method: 'POST', body: JSON.stringify({ id: value.id, secretHash: await hash(value.secret), ip }) });
      }
      const status = /^\/v1\/installations\/([a-f0-9]{32})\/status$/.exec(url.pathname);
      if (status && request.method === 'GET' && !url.search) return obj(env.INSTALLATIONS, status[1]).fetch(new Request('https://internal/status', request));
      const connector = /^\/v1\/installations\/([a-f0-9]{32})\/connect$/.exec(url.pathname);
      if (connector && request.method === 'GET' && !url.search) return obj(env.INSTALLATIONS, connector[1]).fetch(new Request('https://internal/connect', request));
      if (url.pathname === '/api/ws') {
        if (request.method !== 'GET') return R({ error: 'invalid upgrade' }, 400, 'bad_method');
        if (url.search) return R({ error: 'invalid upgrade' }, 400, 'has_query');
        if (request.headers.get('upgrade')?.toLowerCase() !== 'websocket') return R({ error: 'invalid upgrade' }, 400, 'not_upgrade');
        if (request.headers.has('authorization')) return R({ error: 'invalid upgrade' }, 400, 'has_authorization');
        if (request.headers.has('x-destination')) return R({ error: 'invalid upgrade' }, 400, 'has_x_destination');
        const header = request.headers.get('sec-websocket-protocol') || '';
        if (header.length > 512) return R({ error: 'invalid upgrade' }, 400, 'proto_too_long');
        const protocols = header.split(',').map(x => x.trim());
        const flow = protocols.includes(phoneFlowProtocol);
        if (protocols.length !== (flow ? 3 : 2)) return R({ error: 'invalid upgrade' }, 400, 'proto_count');
        if (new Set(protocols).size !== protocols.length || protocols.filter(x => x === stableProtocol).length !== 1) return R({ error: 'invalid upgrade' }, 400, 'proto_dupe');
        const entry = protocols.find(x => x.startsWith(ticketPrefix)); const ticket = entry?.slice(ticketPrefix.length);
        if (!validTicket(ticket)) return R({ error: 'invalid upgrade' }, 400, 'bad_ticket_format');
        if (!flow && env.ALLOW_LEGACY_TEST_TRANSPORT !== 'true') return R({ error: 'phone update required', protocol: phoneFlowProtocol }, 426, 'legacy_426');
        const result = await obj(env.DIRECTORY, `ticket:${await hash(ticket)}`).fetch('https://internal/ticket-consume', { method: 'POST' });
        if (!result.ok) return R({ error: 'unauthorized' }, 401, 'ticket_consume_failed');
        const { id, deviceHash } = await result.json();
        // PHONE_FLOW=off: select the plain gateway protocol even when the phone also offers flow.
        // iOS Build 54's flow client caps its send queue at 16 and tears the socket down on its
        // opening request burst; the plain protocol (same one direct gateways use) has no such cap.
        const useFlow = flow && env.PHONE_FLOW !== 'off';
        const forwarded = await obj(env.INSTALLATIONS, id).fetch('https://internal/open-ws', { method: 'GET', headers: { Upgrade: 'websocket', 'x-relay-ticket': ticket, 'x-relay-device-hash': deviceHash, 'x-relay-phone-flow': useFlow ? 'v1' : 'legacy' } });
        reasons.set(request, forwarded.status === 101 ? 'forward_ok' : 'forward_status_' + forwarded.status);
        return forwarded;
      }
      if (url.pathname.startsWith('/api/')) {
        if (!allowMobileRequest(request.method, url.pathname + url.search)) return reply({ error: 'forbidden' }, 403);
        const authorization = request.headers.get('authorization') || '';
        const token = /^Bearer (cdt_[A-Za-z0-9_-]{16,256})$/.exec(authorization);
        if (!token) return reply({ error: 'unauthorized' }, 401);
        const deviceHash = await hash(token[1]);
        const lookup = await obj(env.DIRECTORY, deviceHash).fetch('https://internal/lookup');
        if (!lookup.ok) return reply({ error: 'unauthorized' }, 401);
        const { id } = await lookup.json();
        if (Number(request.headers.get('content-length') || 0) > 8 * 1024 * 1024) return reply({ error: 'too large' }, 413);
        const headers = { authorization, 'x-relay-device-hash': deviceHash, 'x-relay-method': request.method, 'x-relay-target': url.pathname + url.search, 'x-relay-started': String(started) };
        for (const name of ['content-type', 'accept']) { const value = request.headers.get(name); if (value && value.length <= 1024) headers[name] = value; }
        return obj(env.INSTALLATIONS, id).fetch('https://internal/forward', { method: 'POST', headers, body: request.body });
      }
      return reply({ error: 'not found' }, 404);
    } catch {
      return reply({ error: 'unavailable' }, 503);
    }
  }
};
