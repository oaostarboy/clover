import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

test('no per-message RPC debug logging remains in the worker', () => {
  const src = readFileSync(new URL('../src/worker.mjs', import.meta.url), 'utf8');
  for (const k of ['rpc_up', 'rpc_down', 'rpc_event', 'rpcLog', 'TEMPORARY']) assert.ok(!src.includes(k), k);
});

test('wrangler.toml carries no outage-era settings', () => {
  const toml = readFileSync(new URL('../wrangler.toml', import.meta.url), 'utf8');
  assert.ok(!/ALLOW_LEGACY_TEST_TRANSPORT/.test(toml));
});
