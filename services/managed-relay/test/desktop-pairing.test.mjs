import test from 'node:test';
import assert from 'node:assert/strict';
import { createDesktopPairing } from '../src/desktop-pairing.mjs';

function fixture() {
  const calls = { enroll: 0, open: 0, close: 0, mint: 0, register: [], deleted: [] };
  const connection = { mode: 'local', baseUrl: 'http://127.0.0.1:12345', token: 'local-owner-test' };
  let direct = false, fail = false;
  const request = async (_connection, path, options = {}) => {
    if (path === '/api/mobile/pairing') return { configured: direct, origin: direct ? 'https://existing.example' : null, reason: direct ? null : 'no_public_https_url' };
    if (path === '/api/mobile/relay/devices') return { profile: 'default', gatewayId: 'a'.repeat(64), devices: [{ hash: 'a'.repeat(64), expiresAt: Date.now() + 60000 }] };
    if (path === '/api/mobile/relay/pairing') { calls.mint++; return { configured: true, origin: 'https://relay.cloverai.ca', device: { id: 'test-device' }, uri: 'clover://pair?test', token: 'cdt_fixture' }; }
    if (options.method === 'DELETE') { calls.deleted.push(path); return { ok: true }; }
    throw Error('unexpected path ' + path);
  };
  const service = createDesktopPairing({
    identityPath: scope => '/tmp/fixture-' + scope,
    request,
    enroll: async () => { calls.enroll++; return { identity: { id: 'fixture' } }; },
    connect: async () => { calls.open++; return { replaceDevices: async devices => { if (fail) throw Error('offline'); calls.register.push(devices); }, close: () => { calls.close++; } }; },
    syncIntervalMs: 0,
  });
  return { calls, connection, service, direct: () => { direct = true; }, fail: () => { fail = true; } };
}

test('automatic pairing coalesces connector startup and registers before exposing QR', async t => {
  const f = fixture(); t.after(() => f.service.close());
  const statuses = await Promise.all(Array.from({ length: 10 }, () => f.service.status('local/default', 'default', f.connection)));
  assert.ok(statuses.every(s => s.configured && s.origin === 'https://relay.cloverai.ca'));
  assert.equal(f.calls.enroll, 1); assert.equal(f.calls.open, 1);
  const code = await f.service.pair('local/default', 'default', f.connection);
  assert.equal(code.token, 'cdt_fixture'); assert.equal(f.calls.mint, 1); assert.ok(f.calls.register.length >= 2);
});

test('legacy and registry aliases for the same local profile share one connector', async t => {
  const f = fixture(); t.after(() => f.service.close());
  await Promise.all([
    f.service.status('legacy', 'default', f.connection),
    f.service.status('registry', 'default', f.connection)
  ]);
  assert.equal(f.calls.open, 1);
  assert.equal(f.calls.enroll, 1);
});

test('identity scope survives a local backend port change and request alias change', async t => {
  const paths = [];
  const service = createDesktopPairing({ identityPath: scope => { paths.push(scope); return '/tmp/test'; }, syncIntervalMs: 0,
    enroll: async () => ({ identity: {} }), connect: async () => ({ close() {}, replaceDevices: async () => {} }),
    request: async (_c, path) => path === '/api/mobile/pairing' ? { configured: false } : { profile: 'default', gatewayId: 'a'.repeat(64), devices: [] }
  });
  t.after(() => service.close());
  await service.status('legacy', 'default', { mode: 'local', baseUrl: 'http://127.0.0.1:12345' });
  await service.status('registry', 'default', { mode: 'local', baseUrl: 'http://127.0.0.1:12346' });
  assert.equal(paths.length, 2);
  assert.equal(paths[0], paths[1]);
});

test('different gateway identities with the same profile keep independent connectors', async t => {
  const paths = [], closed = [];
  const service = createDesktopPairing({ identityPath: scope => { paths.push(scope); return scope; }, syncIntervalMs: 0,
    enroll: async (_origin, path) => ({ identity: { path } }),
    connect: async ({ identity }) => ({ close() { closed.push(identity.path); }, replaceDevices: async () => {} }),
    request: async (c, path) => path === '/api/mobile/pairing' ? { configured: false } :
      { profile: 'default', gatewayId: c.gatewayId, devices: [] }
  });
  t.after(() => service.close());
  await service.status('first', 'default', { mode: 'local', baseUrl: 'http://127.0.0.1:12345', gatewayId: 'a'.repeat(64) });
  await service.status('second', 'default', { mode: 'local', baseUrl: 'http://127.0.0.1:12346', gatewayId: 'b'.repeat(64) });
  assert.notEqual(paths[0], paths[1]);
  assert.deepEqual(closed, []);
});

test('a changed gateway identity closes the active connector on refresh', async t => {
  let gatewayId = 'a'.repeat(64), closed = 0;
  const service = createDesktopPairing({ identityPath: () => '/tmp/test', syncIntervalMs: 0,
    enroll: async () => ({ identity: {} }),
    connect: async () => ({ close() { closed++; }, replaceDevices: async () => {} }),
    request: async (_c, path) => path === '/api/mobile/pairing' ? { configured: false } : { profile: 'default', gatewayId, devices: [] }
  });
  t.after(() => service.close());
  await service.status('x', 'default', { mode: 'local', baseUrl: 'http://127.0.0.1:12345' });
  gatewayId = 'b'.repeat(64);
  await assert.rejects(service.refresh('x'), /identity/i);
  assert.equal(closed, 1);
});

test('delayed old backend status cannot replace the newer connector', async t => {
  let release, started, opened = [], closed = [];
  const gate = new Promise(resolve => { release = resolve; });
  const waiting = new Promise(resolve => { started = resolve; });
  const old = { mode: 'local', baseUrl: 'http://127.0.0.1:12345' };
  const newer = { ...old, baseUrl: 'http://127.0.0.1:12346' };
  let pause = false;
  const service = createDesktopPairing({ identityPath: () => '/tmp/test', syncIntervalMs: 0,
    enroll: async () => ({ identity: {} }),
    connect: async ({ backendOrigin }) => { opened.push(backendOrigin); return { close() { closed.push(backendOrigin); }, replaceDevices: async () => {} }; },
    request: async (connection, path) => {
      if (path === '/api/mobile/pairing') { if (pause && connection.baseUrl === old.baseUrl) { started(); await gate; } return { configured: false }; }
      return { profile: 'default', gatewayId: 'a'.repeat(64), devices: [] };
    }
  });
  t.after(() => service.close());
  await service.status('legacy', 'default', old);
  pause = true;
  const pending = service.status('legacy', 'default', old); await waiting;
  await service.status('registry', 'default', newer);
  release();
  await assert.rejects(pending, /replaced/i);
  assert.deepEqual(opened, [old.baseUrl, newer.baseUrl]);
  assert.deepEqual(closed, [old.baseUrl]);
});

test('device capacity is reported before enrollment', async t => {
  let opened = 0;
  const service = createDesktopPairing({ identityPath: () => '/tmp/test', syncIntervalMs: 0,
    enroll: async () => { opened++; return { identity: {} }; },
    request: async (_c, path) => path === '/api/mobile/pairing' ? { configured: false } : { profile: 'default', gatewayId: 'a'.repeat(64), devices: Array(65).fill({ hash: 'a'.repeat(64), expiresAt: Date.now() + 1000 }) }
  });
  t.after(() => service.close());
  await assert.rejects(service.status('x', 'default', { mode: 'local', baseUrl: 'http://127.0.0.1:12345' }), /capacity/i);
  assert.equal(opened, 0);
});

test('configured direct gateway never enrolls or starts a relay', async t => {
  const f = fixture(); t.after(() => f.service.close()); f.direct();
  assert.equal((await f.service.status('x', 'default', f.connection)).origin, 'https://existing.example');
  assert.equal(f.calls.enroll, 0);
});

test('configured direct origin without a key never enrolls or overrides settings', async t => {
  let enrollments = 0;
  const service = createDesktopPairing({ identityPath: () => '/tmp/test', syncIntervalMs: 0,
    enroll: async () => { enrollments++; },
    request: async () => ({ configured: false, origin: 'https://existing.example', reason: 'no_gateway_key' })
  });
  t.after(() => service.close());
  const result = await service.status('x', 'default', { mode: 'local', baseUrl: 'http://127.0.0.1:12345' });
  assert.equal(result.reason, 'no_gateway_key');
  assert.equal(enrollments, 0);
});

test('remote or wrong-profile backends are never attached to relay', async t => {
  const f = fixture(); t.after(() => f.service.close());
  assert.equal((await f.service.status('x', 'default', { ...f.connection, mode: 'remote' })).configured, false);
  await assert.rejects(f.service.status('other', 'other', f.connection), /profile/i);
  assert.equal(f.calls.open, 0);
});

test('failed relay registration revokes newly minted device and never exposes QR', async t => {
  const f = fixture(); t.after(() => f.service.close());
  await f.service.status('x', 'default', f.connection); f.fail();
  await assert.rejects(f.service.pair('x', 'default', f.connection), /offline/);
  assert.deepEqual(f.calls.deleted, ['/api/mobile/devices/test-device']);
});

test('a queued pair does not mint after its backend is replaced', async t => {
  let release, started;
  const waiting = new Promise(resolve => { started = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  let reads = 0, mints = 0;
  const connection = { mode: 'local', baseUrl: 'http://127.0.0.1:12345' };
  const service = createDesktopPairing({ identityPath: () => '/tmp/test', syncIntervalMs: 0,
    enroll: async () => ({ identity: {} }), connect: async () => ({ close() {}, replaceDevices: async () => {} }),
    request: async (_c, path) => {
      if (path === '/api/mobile/pairing') return { configured: false };
      if (path === '/api/mobile/relay/devices') { if (++reads === 3) { started(); await gate; } return { profile: 'default', gatewayId: 'a'.repeat(64), devices: [] }; }
      if (path === '/api/mobile/relay/pairing') { mints++; return { device: { id: 'x' } }; }
      return {};
    }
  });
  t.after(() => service.close());
  await service.status('x', 'default', connection);
  const refresh = service.refresh('x'); await waiting;
  const pair = service.pair('x', 'default', connection);
  await new Promise(resolve => setImmediate(resolve));
  await service.status('x', 'default', { ...connection, baseUrl: 'http://127.0.0.1:12346' });
  release();
  await assert.rejects(refresh);
  await assert.rejects(pair);
  assert.equal(mints, 0);
});

test('disposed registration cannot report successful pairing', async t => {
  let release, started, registrations = 0;
  const gate = new Promise(resolve => { release = resolve; });
  const waiting = new Promise(resolve => { started = resolve; });
  const connection = { mode: 'local', baseUrl: 'http://127.0.0.1:12345' };
  const service = createDesktopPairing({ identityPath: () => '/tmp/test', syncIntervalMs: 0,
    enroll: async () => ({ identity: {} }), connect: async () => ({ close() {}, replaceDevices: async () => { if (++registrations === 2) { started(); await gate; } } }),
    request: async (_c, path) => {
      if (path === '/api/mobile/pairing') return { configured: false };
      if (path === '/api/mobile/relay/devices') return { profile: 'default', gatewayId: 'a'.repeat(64), devices: [] };
      if (path === '/api/mobile/relay/pairing') return { device: { id: 'x' } };
      return {};
    }
  });
  t.after(() => service.close());
  await service.status('x', 'default', connection);
  const pair = service.pair('x', 'default', connection); await waiting;
  await service.status('x', 'default', { ...connection, baseUrl: 'http://127.0.0.1:12346' });
  release();
  await assert.rejects(pair, /stopped/i);
});

test('rollback failure still closes connector after failed registration', async t => {
  let closed = 0, fail = false;
  const connection = { mode: 'local', baseUrl: 'http://127.0.0.1:12345' };
  const service = createDesktopPairing({
    identityPath: () => '/tmp/fixture', syncIntervalMs: 0,
    enroll: async () => ({ identity: {} }),
    connect: async () => ({ close: () => { closed++; }, replaceDevices: async () => { if (fail) throw Error('registration failed'); } }),
    request: async (_c, path, options = {}) => {
      if (path === '/api/mobile/pairing') return { configured: false };
      if (path === '/api/mobile/relay/devices') return { profile: 'default', gatewayId: 'a'.repeat(64), devices: [] };
      if (path === '/api/mobile/relay/pairing') return { device: { id: 'test' } };
      if (options.method === 'DELETE') throw Error('rollback failed');
      throw Error('unexpected');
    }
  });
  t.after(() => service.close());
  await service.status('x', 'default', connection);
  fail = true;
  await assert.rejects(service.pair('x', 'default', connection));
  assert.equal(closed, 1);
});

test('backend replacement closes old connector without stopping either gateway', async t => {
  const f = fixture(); t.after(() => f.service.close());
  await f.service.status('x', 'default', f.connection);
  await f.service.status('x', 'default', { ...f.connection, baseUrl: 'http://127.0.0.1:12346' });
  assert.equal(f.calls.open, 2); assert.equal(f.calls.close, 1);
});

test('a failed device-list snapshot keeps the connector open and keeps retrying', async t => {
  let fail = false, snaps = 0, closes = 0, errors = 0;
  const connection = { mode: 'local', baseUrl: 'http://127.0.0.1:12345', token: 'x' };
  const request = async (_c, path) => {
    if (path === '/api/mobile/pairing') return { configured: false, origin: null };
    snaps++; if (fail) throw Error('devices timeout');
    return { profile: 'default', gatewayId: 'a'.repeat(64), devices: [] };
  };
  const service = createDesktopPairing({ identityPath: () => '/tmp/x', request, syncIntervalMs: 5, backoffMinMs: 5, backoffMaxMs: 40, onSyncError: () => { errors++; },
    enroll: async () => ({ identity: {} }), connect: async () => ({ replaceDevices: async () => {}, close: () => { closes++; } }) });
  t.after(() => service.close());
  await service.status('s', 'default', connection);
  fail = true;
  await new Promise(r => setTimeout(r, 400));
  assert.equal(closes, 0); assert.ok(errors >= 2);
  fail = false; const before = snaps;
  await new Promise(r => setTimeout(r, 700));
  assert.ok(snaps > before + 1); assert.equal(closes, 0);
});
