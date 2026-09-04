import assert from 'node:assert/strict';
import test from 'node:test';

import { relayBackendResponse, requireMinerAuth } from '../services/authProxy.js';

function mockResponse() {
  return {
    cookies: [],
    statusCode: 200,
    body: null,
    append(name, value) {
      if (name.toLowerCase() === 'set-cookie') {
        this.cookies.push(value);
      }
    },
    status(code) {
      this.statusCode = code;
      return this;
    },
    json(payload) {
      this.body = payload;
      return this;
    },
  };
}

test('relayBackendResponse forwards set-cookie and status', () => {
  const upstream = {
    status: 200,
    body: { success: true, data: { authenticated: true, username: 'admin' } },
    setCookies: ['session=abc; HttpOnly; Path=/'],
  };
  const res = mockResponse();

  relayBackendResponse(res, upstream);

  assert.deepEqual(res.cookies, upstream.setCookies);
  assert.equal(res.statusCode, 200);
  assert.deepEqual(res.body, upstream.body);
});

test('requireMinerAuth returns 401 when backend session is invalid', async () => {
  const req = { headers: { cookie: '' } };
  const res = mockResponse();
  let nextCalled = false;

  await requireMinerAuth({
    sessionApi: async () => ({ status: 401, body: { success: false } }),
  })(req, res, () => {
    nextCalled = true;
  });

  assert.equal(nextCalled, false);
  assert.equal(res.statusCode, 401);
  assert.equal(res.body.success, false);
});
