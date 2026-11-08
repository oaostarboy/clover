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
