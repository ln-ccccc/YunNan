import assert from 'node:assert/strict';
import test from 'node:test';

import { resolveInitialView } from '../src/auth/authGuards.js';

test('resolveInitialView returns login when not authenticated', () => {
  assert.equal(resolveInitialView({ authenticated: false, hash: '#/map/1' }), 'login');
});

test('resolveInitialView keeps requested view when authenticated', () => {
  assert.equal(resolveInitialView({ authenticated: true, hash: '#/map/1' }), 'map');
});
