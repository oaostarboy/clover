import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { allowMobileRequest } from '../src/admission.mjs';

test('permits native ws ticket POST without a query', () => {
  assert.equal(allowMobileRequest('POST', '/api/auth/ws-ticket'), true);
});
test('rejects admin paths and encoded path aliases', () => {
  for (const path of ['/api/config', '/api/%61uth/ws-ticket', '/api/auth/ws-ticket/']) {
    assert.equal(allowMobileRequest('POST', path), false);
  }
});
test('restricts sessions queries and messages paths', () => {
  assert.equal(allowMobileRequest('GET', '/api/sessions?limit=20&order=desc'), true);
  assert.equal(allowMobileRequest('GET', '/api/sessions?token=x'), false);
  assert.equal(allowMobileRequest('GET', '/api/sessions/abc/messages'), true);
  assert.equal(allowMobileRequest('GET', '/api/sessions/abc/messages?token=x'), false);
});
test('permits audio profile query only when exact and unambiguous', () => {
  assert.equal(allowMobileRequest('POST', '/api/audio/speak?profile=work'), true);
  assert.equal(allowMobileRequest('POST', '/api/audio/speak?profile=work&profile=work'), false);
  assert.equal(allowMobileRequest('POST', '/api/audio/speak?token=x'), false);
});
test('admits only still-screenshot desk GETs', () => {
  assert.equal(allowMobileRequest('GET', '/desk/displays'), true);
  assert.equal(allowMobileRequest('GET', '/desk/snapshot?display=1&q=60&w=800'), true);
  assert.equal(allowMobileRequest('GET', '/desk/snapshot'), true);
  for (const p of ['/desk/snapshot?display=1&x=0&y=10&cw=99999&ch=1080&q=100&w=3840', '/desk/snapshot?q=1&w=0', '/desk/snapshot?x=00001']) assert.equal(allowMobileRequest('GET', p), true, p);
  for (const p of ['/desk/snapshot?x=-1', '/desk/snapshot?x=100000', '/desk/snapshot?cw=1.5', '/desk/snapshot?ch=abc', '/desk/snapshot?y=', '/desk/snapshot?q=0', '/desk/snapshot?q=101', '/desk/snapshot?q=1000', '/desk/snapshot?w=12345', '/desk/snapshot?w=-5', '/desk/snapshot?x=1&x=2', '/desk/snapshot?z=1']) assert.equal(allowMobileRequest('GET', p), false, p);
  for (const [m, p] of [['GET', '/desk/stream'], ['POST', '/desk/snapshot'], ['POST', '/desk/input'], ['GET', '/desk/snapshot?z=1'], ['GET', '/desk/snapshot?display=1&display=2'], ['GET', '/desk/displays?a=1'], ['GET', '/desk/snapshot/'], ['GET', '/desk/%73napshot']]) assert.equal(allowMobileRequest(m, p), false, p);
});

test('carries Creations downloads and thumbnails with exact queries only', () => {
  assert.equal(allowMobileRequest('GET', '/api/files/download?path=%2Fhome%2Fa%2Fpic.png'), true);
  assert.equal(allowMobileRequest('GET', '/api/files/download'), false);
  assert.equal(allowMobileRequest('GET', '/api/files/download?path=%2Fa&token=x'), false);
  assert.equal(allowMobileRequest('GET', '/api/files/download?path=%2Fa&path=%2Fb'), false);
  assert.equal(allowMobileRequest('GET', '/api/files/download?path=relative.png'), false);
  assert.equal(allowMobileRequest('POST', '/api/files/download?path=%2Fa'), false);
  assert.equal(allowMobileRequest('GET', '/api/files/upload'), false);
  assert.equal(allowMobileRequest('GET', '/api/files/stream?path=%2Fa'), false);
  assert.equal(allowMobileRequest('GET', '/api/artifacts/thumb?id=0123456789abcdef'), true);
  assert.equal(allowMobileRequest('GET', '/api/artifacts/thumb?id=0123456789abcdef&size=320'), true);
  assert.equal(allowMobileRequest('GET', '/api/artifacts/thumb?id=../etc'), false);
  assert.equal(allowMobileRequest('GET', '/api/artifacts/thumb?id=0123456789abcdef&x=1'), false);
});
test('desk input never rides HTTP; it is a JSON-RPC method on the phone socket', () => {
  for (const p of ['/desk/stream', '/desk/input', '/api/desk/input']) assert.equal(allowMobileRequest('POST', p), false);
  assert.equal(allowMobileRequest('GET', '/desk/stream'), false);
});

test('Creations media paths are admitted with exact queries only', () => {
  assert.equal(allowMobileRequest('GET', '/api/files/download?path=/tmp/a.png'), true);
  assert.equal(allowMobileRequest('GET', '/api/files/download?path=/tmp/a.png&token=x'), false);
  assert.equal(allowMobileRequest('GET', '/api/artifacts/thumb?id=0123456789abcdef&size=480'), true);
  assert.equal(allowMobileRequest('GET', '/api/artifacts/thumb?id=nothex&size=480'), false);
  assert.equal(allowMobileRequest('POST', '/api/files/download?path=/tmp/a.png'), false);
});
