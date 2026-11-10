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
  for (const [m, p] of [['GET', '/desk/stream'], ['POST', '/desk/snapshot'], ['POST', '/desk/input'], ['GET', '/desk/snapshot?x=1'], ['GET', '/desk/snapshot?display=1&display=2'], ['GET', '/desk/displays?a=1'], ['GET', '/desk/snapshot/'], ['GET', '/desk/%73napshot']]) assert.equal(allowMobileRequest(m, p), false, p);
});
