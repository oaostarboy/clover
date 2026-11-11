import { ensureEnrolled, openConnector } from './desktop-connector.mjs';

export const MANAGED_RELAY_ORIGIN = 'https://relay.cloverai.ca';

// Electron owns this lifecycle. It reuses the authenticated, profile-bound
// backend descriptor supplied by the existing desktop startup machinery.
// No backend is spawned, killed, reconfigured, or promoted to owner authority.
export function createDesktopPairing({ identityPath, request, enroll = ensureEnrolled,
  connect = openConnector, syncIntervalMs = 2000, onSyncError, backoffMinMs = 1000, backoffMaxMs = 30000,
  random = Math.random, sleep = ms => new Promise(resolve => setTimeout(resolve, ms)) }) {
  const entries = new Map();
  const aliases = new Map();
  const generations = new Map();
  let sequence = 0;
  let stopped = false;
  // Real auth/identity rejections by the relay (401/403) stop retrying and are
  // reported through the pairing status. Everything else (5xx, network,
  // timeouts, 1011/1012 closes) is transient and retried forever.
  const rejected = new Map();
  const isRejection = error => /(?:enrollment failed:|Unexpected server response:) (?:401|403)\b/.test(String(error?.message || error));
  // Jittered exponential backoff: 1 s -> 30 s cap, 50-100 % of the nominal delay.
  const backoff = failures => Math.round(Math.min(backoffMaxMs, backoffMinMs * 2 ** Math.min(Math.max(failures - 1, 0), 16)) * (0.5 + random() / 2));
  const rejection = error => ({ configured: false, reason: 'relay_auth_rejected', origin: MANAGED_RELAY_ORIGIN, managed: true, error: String(error?.message || error).slice(0, 200) });
  const local = c => {
    try { const u = new URL(c.baseUrl); return c.mode === 'local' && u.protocol === 'http:' && ['127.0.0.1', '[::1]'].includes(u.hostname) && !u.username && !u.password && u.pathname === '/' && !u.search && !u.hash; }
    catch { return false; }
  };
  const descriptor = c => `${c.baseUrl}\n${c.token || ''}`;
  const dispose = e => { e.disposed = true; clearInterval(e.timer); e.connector?.close(); e.connector = null; };
  async function snapshot(e) {
    const data = await request(e.connection, '/api/mobile/relay/devices');
    e.verifying = true;
    if (data.profile !== e.profile || !/^[0-9a-f]{64}$/.test(data.gatewayId || '') || (e.gatewayId && data.gatewayId !== e.gatewayId) || !Array.isArray(data.devices)) throw Error('Mobile gateway identity or profile verification failed');
    if (data.devices.length > 64) throw Error('Mobile relay device capacity exceeded (maximum 64); revoke a device and retry');
    return data;
  }
  async function synchronize(e) {
    let devices;
    e.verifying = false;
    try { ({ devices } = await snapshot(e)); }
    catch (error) {
      // Transport/timeout failures keep the live connector and last good list; identity/profile/capacity rejections close it.
      if (e.verifying) { e.connector?.close(); e.connector = null; }
      throw error;
    }
    if (e.disposed || stopped) throw Error('Pairing stopped');
    if (!e.connector) {
      const { identity } = await enroll(MANAGED_RELAY_ORIGIN, identityPath(e.scope));
      if (e.disposed || stopped) throw Error('Pairing stopped');
      const connector = await connect({ relayOrigin: MANAGED_RELAY_ORIGIN, identity, backendOrigin: e.connection.baseUrl });
      if (e.disposed || stopped) { connector.close(); throw Error('Pairing stopped'); }
      e.connector = connector;
    }
    try { await e.connector.replaceDevices(devices); }
    catch (error) { e.connector?.close(); e.connector = null; throw error; }
    if (e.disposed || stopped) throw Error('Pairing stopped');
    e.failures = 0;
  }
  function serial(e, operation) {
    const next = e.queue.then(operation);
    e.queue = next.catch(() => {});
    return next;
  }
  async function ensure(scope, profile, connection, gatewayId) {
    if (stopped) throw Error('Pairing stopped');
    let e = entries.get(scope);
    if (e && descriptor(e.connection) !== descriptor(connection)) { dispose(e); entries.delete(scope); e = null; }
    if (!e) {
      e = { scope, profile, gatewayId, connection, connector: null, queue: Promise.resolve(), failures: 0, disposed: false, timer: null, ticking: false };
      entries.set(scope, e);
      e.ready = serial(e, () => synchronize(e)).catch(error => { dispose(e); if (entries.get(scope) === e) entries.delete(scope); if (isRejection(error)) rejected.set(scope, rejection(error)); throw error; });
      if (syncIntervalMs > 0) {
        e.timer = setInterval(() => {
          if (e.disposed || e.ticking || Date.now() < (e.nextAt || 0)) return;
          e.ticking = true;
          void serial(e, () => synchronize(e)).then(() => { e.nextAt = Date.now() + syncIntervalMs; }).catch(error => {
            if (e.disposed || stopped) return;
            if (isRejection(error)) { rejected.set(e.scope, rejection(error)); dispose(e); if (entries.get(e.scope) === e) entries.delete(e.scope); onSyncError?.(error, e.failures, true); return; }
            // synchronize() already closed the connector if the relay link itself failed; a local snapshot hiccup keeps it.
            e.failures++; // never give up: jittered backoff 1 s -> 30 s; reset only after the relay acked the device list
            e.nextAt = Date.now() + backoff(e.failures);
            onSyncError?.(error, e.failures, false);
          }).finally(() => { e.ticking = false; });
        }, Math.min(syncIntervalMs, 1000));
        e.timer.unref?.();
      }
    }
    await e.ready;
    if (!e.connector) await serial(e, () => synchronize(e));
    return e;
  }
  async function state(scope, profile, connection, retryRejected = false) {
    const generation = ++sequence;
    const direct = await request(connection, '/api/mobile/pairing');
    if (direct.configured || direct.origin) {
      const oldScope = aliases.get(scope) || scope;
      const old = entries.get(oldScope); if (old && descriptor(old.connection) === descriptor(connection)) { dispose(old); entries.delete(oldScope); }
      return { direct };
    }
    if (!local(connection)) return { direct };
    const { gatewayId } = await snapshot({ connection, profile });
    const canonical = JSON.stringify(['local-gateway', gatewayId, profile]);
    if (retryRejected) rejected.delete(canonical);
    if (rejected.has(canonical)) return { direct: rejected.get(canonical) };
    const previous = generations.get(canonical);
    if (previous && previous.generation > generation && previous.descriptor !== descriptor(connection)) throw Error('Pairing backend replaced');
    if (!previous || previous.generation < generation) generations.set(canonical, { generation, descriptor: descriptor(connection) });
    aliases.set(scope, canonical);
    const entry = await ensure(canonical, profile, connection, gatewayId);
    return { entry };
  }
  return {
    async status(scope, profile, connection) {
      const { direct } = await state(scope, profile, connection);
      return direct || { configured: true, reason: null, origin: MANAGED_RELAY_ORIGIN, managed: true };
    },
    async pair(scope, profile, connection) {
      const { direct, entry } = await state(scope, profile, connection, true);
      if (direct) return direct.configured ? request(connection, '/api/mobile/pairing', { method: 'POST' }) : direct;
      return serial(entry, async () => {
        if (entry.disposed || stopped) throw Error('Pairing stopped');
        const code = await request(connection, '/api/mobile/relay/pairing', { method: 'POST' });
        try {
          if (entry.disposed || stopped) throw Error('Pairing stopped');
          await synchronize(entry);
          return code;
        } catch (error) {
          try {
            if (code.device?.id) await request(connection, `/api/mobile/devices/${encodeURIComponent(code.device.id)}`, { method: 'DELETE' });
          } finally {
            entry.connector?.close(); entry.connector = null;
          }
          throw error;
        }
      });
    },
    async refresh(scope) {
      const e = entries.get(aliases.get(scope) || scope); if (e) await serial(e, () => synchronize(e));
    },
    // After sleep/resume or a network change sockets are suspect: drop them and
    // reconnect now (also gives a rejected installation another attempt).
    reconnect() {
      rejected.clear();
      for (const e of entries.values()) {
        if (e.disposed) continue;
        e.connector?.close(); e.connector = null; e.failures = 0; e.nextAt = 0;
        if (!e.ticking) { e.ticking = true; void serial(e, () => synchronize(e)).then(() => { e.nextAt = Date.now() + syncIntervalMs; }).catch(error => { e.failures++; e.nextAt = Date.now() + backoff(e.failures); onSyncError?.(error, e.failures, false); }).finally(() => { e.ticking = false; }); }
      }
    },
    // Launch supervisor: keep trying until the link is up (or the relay
    // rejects this installation). Transient failures never end the loop.
    async keepAlive(scope, profile, getConnection) {
      for (let attempt = 1; !stopped; attempt++) {
        try { const status = await this.status(scope, profile, await getConnection()); return status; }
        catch (error) { onSyncError?.(error, attempt, false); await sleep(backoff(attempt)); }
      }
    },
    // Aggregate live phone-side activity across every connector (see openConnector().activity()).
    activity() {
      const total = { chatChannels: 0, deskChannels: 0, httpJobs: 0, lastChatDataAt: 0 };
      for (const e of entries.values()) {
        const a = e.connector?.activity?.(); if (!a) continue;
        total.chatChannels += a.chatChannels; total.deskChannels += a.deskChannels; total.httpJobs += a.httpJobs;
        total.lastChatDataAt = Math.max(total.lastChatDataAt, a.lastChatDataAt);
      }
      return total;
    },
    close() { stopped = true; for (const e of entries.values()) dispose(e); entries.clear(); aliases.clear(); }
  };
}
