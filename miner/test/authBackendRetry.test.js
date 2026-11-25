import assert from 'node:assert/strict';
import test from 'node:test';

import { requestBackendAuth } from '../services/authBackend.js';

function connectionResetError() {
  const cause = new Error('read ECONNRESET');
  cause.code = 'ECONNRESET';
  const error = new TypeError('fetch failed');
  error.cause = cause;
  return error;
}

function socketClosedError() {
  // 线上真实形态（Node 18 自带 undici：gunicorn 关闭了被连接池复用的 keep-alive 连接）
  const cause = new Error('other side closed');
  cause.code = 'UND_ERR_SOCKET';
  const error = new TypeError('fetch failed');
  error.cause = cause;
  return error;
}

function jsonResponse(payload) {
  return {
    status: 200,
    text: async () => JSON.stringify(payload),
    headers: { getSetCookie: () => [], get: () => null },
  };
}

test('瞬态连接错误（ECONNRESET）单次重试后成功', async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    if (calls === 1) throw connectionResetError();
    return jsonResponse({ success: true });
  };
  try {
    const result = await requestBackendAuth('GET', '/api/auth/session');
    assert.equal(calls, 2);
    assert.equal(result.body.success, true);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('UND_ERR_SOCKET（收到响应前连接被对端关闭）同样重试一次后成功', async () => {
  // 此前不在瞬态集合里：连 300ms 的重试都不走，直接变成 502（2026-11-24，线上日志 9 次）
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    if (calls === 1) throw socketClosedError();
    return jsonResponse({ success: true });
  };
  try {
    const result = await requestBackendAuth('GET', '/api/auth/session');
    assert.equal(calls, 2);
    assert.equal(result.body.success, true);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('连续瞬态错误重试一次后仍失败则上抛', async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    throw connectionResetError();
  };
  try {
    await assert.rejects(() => requestBackendAuth('GET', '/api/auth/session'), TypeError);
    assert.equal(calls, 2);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('非瞬态错误（如 DNS ENOTFOUND）不重试', async () => {
  const originalFetch = globalThis.fetch;
  const cause = new Error('getaddrinfo ENOTFOUND');
  cause.code = 'ENOTFOUND';
  const error = new TypeError('fetch failed');
  error.cause = cause;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    throw error;
  };
  try {
    await assert.rejects(() => requestBackendAuth('GET', '/api/auth/session'));
    assert.equal(calls, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('HTTP 5xx 响应不属于连接错误，不重试', async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return jsonResponse({ success: false, msg: 'upstream 500' });
  };
  try {
    const result = await requestBackendAuth('GET', '/api/auth/session');
    assert.equal(calls, 1);
    assert.equal(result.body.success, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
