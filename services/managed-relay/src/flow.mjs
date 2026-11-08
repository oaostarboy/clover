// Explicit opt-in stop-and-wait transport; production relay integration pending.
export const FLOW_LIMITS = Object.freeze({ messageBytes: 1048576, chunkBytes: 16384, wireChars: 23000 });
const nonce = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
const validNonce = x => typeof x === 'string' && /^[a-f0-9]{32}$/.test(x);
const encode = bytes => btoa(String.fromCharCode(...bytes));
const decode = str => Uint8Array.from(atob(str), c => c.charCodeAt(0));
export class FlowTransport {
  constructor({ send, onMessage, onClose = () => {}, onWritable = () => {}, timeoutMs = 30000, maxMessageBytes = FLOW_LIMITS.messageBytes, reserve = () => () => {} }) {
    this.reserve = reserve;
    if (!Number.isSafeInteger(maxMessageBytes) || maxMessageBytes < 1 || maxMessageBytes > 12582912) throw Error('invalid message budget');
    this.maxMessageBytes = maxMessageBytes;
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 10 || timeoutMs > 120000) throw Error('invalid timeout');
    this.timeoutMs = timeoutMs; this.timers = new Map();
    this.send = send; this.onMessage = onMessage; this.onClose = onClose; this.onWritable = onWritable;
    this.closed = false; this.started = false; this.grant = null; this.peerGrant = null;
    this.pending = null; this.tx = null; this.rx = null; this.receiving = false;
  }
  emit(value) { if (!this.closed) { try { this.send(JSON.stringify(value)); } catch { this.close('send failed'); } } }
  clearTimer(key) { clearTimeout(this.timers.get(key)); this.timers.delete(key); }
  arm(key) { this.clearTimer(key); this.timers.set(key, setTimeout(() => this.close('transport timeout'), this.timeoutMs)); }
  start() { if (this.started || this.closed) return; this.started = true; this.grant = nonce(); this.arm('hello'); this.emit({ v: 1, t: 'hello', g: this.grant }); }
  trySend(data) {
    this.sendFailure = null;
    if (!this.started || this.closed || this.tx || !this.peerGrant) return false;
    const binary = typeof data !== 'string';
    if (binary && !(data instanceof Uint8Array)) return false;
    if (data.length > this.maxMessageBytes) return false;
    const release = this.reserve(binary ? data.length : data.length * 3);
    if (!release) { this.sendFailure = 'memory capacity'; return false; }
    let bytes;
    try { bytes = binary ? data.slice() : new TextEncoder().encode(data); }
    catch (error) { release(); throw error; }
    if (bytes.length > this.maxMessageBytes) { release(); return false; }
    this.tx = { bytes, binary, message: nonce(), offset: 0, index: 0 }; this.txRelease = release;
    this.flush(); return !this.closed;
  }
  flush() {
    if (this.closed || !this.tx || this.pending || !this.peerGrant) return;
    const x = this.tx; const end = Math.min(x.offset + FLOW_LIMITS.chunkBytes, x.bytes.length);
    const m = { v: 1, t: 'data', g: this.peerGrant, n: nonce(), m: x.message, i: x.index++, z: x.bytes.length, b: x.binary, f: end === x.bytes.length, d: encode(x.bytes.subarray(x.offset, end)) };
    x.offset = end; this.pending = { nonce: m.n, final: m.f }; this.peerGrant = null;
    this.arm('send'); this.emit(m);
  }
  ack(n) { this.grant = nonce(); this.emit({ v: 1, t: 'ack', n, g: this.grant }); }
  receive(raw) {
    if (this.closed || !this.started) return;
    try {
      if (typeof raw !== 'string' || raw.length > FLOW_LIMITS.wireChars) throw Error();
      const m = JSON.parse(raw);
      if (!m || m.v !== 1) throw Error();
      const shape = { hello: 'g,t,v', ack: 'g,n,t,v', data: 'b,d,f,g,i,m,n,t,v,z' };
      if (!Object.hasOwn(shape, m.t) || Object.keys(m).sort().join(',') !== shape[m.t] || (m.t !== 'data' && raw.length > 192) || (!this.peerHello && m.t !== 'hello')) throw Error();
      if (m.t === 'hello') {
        if (this.peerHello || !validNonce(m.g)) throw Error();
        this.clearTimer('hello'); this.peerHello = true; this.peerGrant = m.g; this.onWritable();
      } else if (m.t === 'ack') {
        if (!this.pending || m.n !== this.pending.nonce || !validNonce(m.g)) throw Error();
        this.clearTimer('send'); const final = this.pending.final; this.pending = null; this.peerGrant = m.g;
        if (final) { this.tx = null; this.txRelease?.(); this.txRelease = null; this.onWritable(); } else this.flush();
      } else if (m.t === 'data') {
        if (!this.grant || m.g !== this.grant || this.receiving || !validNonce(m.n) || !validNonce(m.m) || typeof m.b !== 'boolean' || typeof m.f !== 'boolean' || !Number.isSafeInteger(m.z) || m.z < 0 || m.z > this.maxMessageBytes || !Number.isSafeInteger(m.i) || m.i < 0 || typeof m.d !== 'string' || m.d.length > 21848) throw Error();
        const chunk = decode(m.d);
        if (chunk.length > FLOW_LIMITS.chunkBytes || encode(chunk) !== m.d) throw Error();
        if (!this.rx) {
          if (m.i !== 0) throw Error();
          const release = this.reserve(m.z * (m.b ? 1 : 3));
          if (!release) throw Error('memory capacity');
          this.rxRelease = release;
          this.rx = { bytes: new Uint8Array(m.z), message: m.m, binary: m.b, offset: 0, index: 0 };
        }
        const x = this.rx;
        if (x.message !== m.m || x.binary !== m.b || x.bytes.length !== m.z || x.index !== m.i || chunk.length !== Math.min(FLOW_LIMITS.chunkBytes, m.z - x.offset) || m.f !== (x.offset + chunk.length === m.z)) throw Error();
        this.arm('receive'); this.grant = null; x.bytes.set(chunk, x.offset); x.offset += chunk.length; x.index++;
        if (!m.f) { this.ack(m.n); return; }
        this.rx = null; this.receiving = true; let released = false;
        const payload = x.binary ? x.bytes : new TextDecoder('utf-8', { fatal: true }).decode(x.bytes);
        const releaseReservation = this.rxRelease; this.rxRelease = null;
        try {
          this.onMessage(payload, () => {
            if (released) return; released = true; releaseReservation();
            if (this.closed) return; this.clearTimer('receive'); this.receiving = false; this.ack(m.n);
          });
        } catch (error) { if (!released) { released = true; releaseReservation(); } throw error; }
      } else throw Error();
    } catch { this.close('invalid transport'); }
  }
  close(reason = 'closed') {
    if (this.closed) return; this.closed = true; for (const timer of this.timers.values()) clearTimeout(timer); this.timers.clear(); this.pending = null; this.tx = null; this.txRelease?.(); this.txRelease = null; this.rx = null; this.rxRelease?.(); this.rxRelease = null;
    this.grant = null; this.peerGrant = null; this.receiving = false; this.onClose(reason);
  }
}
