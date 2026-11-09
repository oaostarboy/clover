import { FlowTransport, FLOW_LIMITS } from './flow.mjs';

function utf8Bytes(value) {
  let bytes = 0;
  for (let i = 0; i < value.length; i++) {
    const c = value.charCodeAt(i);
    if (c < 0x80) bytes++;
    else if (c < 0x800) bytes += 2;
    else if (c >= 0xd800 && c <= 0xdbff && i + 1 < value.length && value.charCodeAt(i + 1) >= 0xdc00 && value.charCodeAt(i + 1) <= 0xdfff) { bytes += 4; i++; }
    else bytes += 3;
  }
  return bytes;
}

// A bounded application queue around the negotiated wire protocol. Construct only
// after the endpoint has selected a flow subprotocol; never wrap legacy sockets.
export class FlowSocket {
  constructor(raw, { queueBytes = 1048576, queueCount = 16, timeoutMs = 30000, maxMessageBytes = FLOW_LIMITS.messageBytes, reserve = () => () => {}, closeOnCapacity = true } = {}) {
    if (!Number.isSafeInteger(queueBytes) || queueBytes < 0 || queueBytes > 12582912 || !Number.isSafeInteger(queueCount) || queueCount < 1 || queueCount > 64) throw Error('invalid queue budget');
    this.raw = raw; this.queueBytes = queueBytes; this.queueCount = queueCount; this.maxMessageBytes = maxMessageBytes; this.reserve = reserve; this.closeOnCapacity = closeOnCapacity;
    this.queue = []; this.queuedBytes = 0; this.listeners = new Map(); this.closed = false;
    this.flow = new FlowTransport({ timeoutMs, maxMessageBytes, reserve, send: wire => raw.send(wire),
      onWritable: () => this.drain(), onClose: () => this.close(1008, 'transport closed'),
      onMessage: (data, release) => {
        this.dispatch('message', { data }).then(() => release(), () => { this.close(1008, 'consumer failed'); release(); });
      } });
    raw.addEventListener('message', e => this.flow.receive(e.data));
    raw.addEventListener('open', () => this.flow.start());
    raw.addEventListener('close', e => this.finish(e));
    raw.addEventListener('error', () => this.close(1011, 'transport error'));
    if (raw.readyState === 1) this.flow.start();
  }
  get readyState() { return this.closed ? 3 : this.raw.readyState; }
  get protocol() { return this.raw.protocol; }
  get bufferedAmount() { return this.queuedBytes + (this.flow.tx?.bytes.length || 0); }
  addEventListener(type, fn, options = {}) {
    const list = this.listeners.get(type) || []; if (list.some(x => x.fn === fn)) return;
    if (list.length >= 16) throw Error('listener capacity');
    list.push({ fn, once: !!options.once }); this.listeners.set(type, list);
  }
  removeEventListener(type, fn) { const list = this.listeners.get(type); if (list) this.listeners.set(type, list.filter(x => x.fn !== fn)); }
  async dispatch(type, event) {
    const list = [...(this.listeners.get(type) || [])];
    for (const item of list) {
      if (item.once) this.removeEventListener(type, item.fn);
      await item.fn(event);
    }
  }
  send(data) {
    if (this.closed) throw Error('transport closed');
    if (typeof data !== 'string' && !(data instanceof Uint8Array) && !(data instanceof ArrayBuffer)) throw Error('invalid payload');
    if (data instanceof ArrayBuffer) data = new Uint8Array(data);
    if (data.length > this.maxMessageBytes) throw Error('message capacity');
    const bytes = typeof data === 'string' ? utf8Bytes(data) : data.byteLength;
    if (bytes > this.maxMessageBytes) throw Error('message capacity');
    if (!this.queue.length) {
      if (this.flow.trySend(data)) return;
      if (this.flow.sendFailure === 'memory capacity') { if (this.closeOnCapacity) this.close(1008, 'memory capacity'); throw Error('memory capacity'); }
    }
    if (this.flow.closed) throw Error('transport closed');
    if (this.queue.length >= this.queueCount || this.queuedBytes + bytes > this.queueBytes) throw Error('queue capacity');
    const release = this.reserve(typeof data === 'string' ? data.length * 2 + bytes : bytes);
    if (!release) { if (this.closeOnCapacity) this.close(1008, 'memory capacity'); throw Error('memory capacity'); }
    try { this.queue.push({ data: typeof data === 'string' ? data : data.slice(), bytes, release }); this.queuedBytes += bytes; }
    catch (error) { release(); throw error; }
  }
  drain() {
    if (this.closed) return;
    if (this.queue.length) {
      const item = this.queue[0];
      if (this.flow.trySend(item.data)) { this.queue.shift(); this.queuedBytes -= item.bytes; item.release(); }
      else if (this.flow.sendFailure === 'memory capacity') { if (this.closeOnCapacity) { this.close(1008, 'memory capacity'); return; } setTimeout(() => this.drain(), 50); return; }
    }
    if (!this.queue.length && !this.flow.tx) void this.dispatch('drain', {}).catch(() => this.close(1011, 'consumer failed'));
  }
  finish(event = {}) {
    if (this.closed) return;
    this.closed = true; for (const item of this.queue) item.release(); this.queue.length = 0; this.queuedBytes = 0; this.flow.close();
    void this.dispatch('close', event).finally(() => this.listeners.clear()).catch(() => {});
  }
  close(code = 1000, reason = '') {
    if (this.closed) return;
    this.finish({ code, reason }); try { this.raw.close(code, reason); } catch {}
  }
}
