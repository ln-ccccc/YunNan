import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createTileFallbackState,
  noteTileError,
  noteTileLoad,
} from '../src/map/tileFallbackPolicy.js';

test('does not fallback after at least one tile has loaded successfully', () => {
  const state = createTileFallbackState();

  noteTileLoad(state);
  const shouldFallback = noteTileError(state, { initialErrorThreshold: 2 });

  assert.equal(shouldFallback, false);
  assert.equal(state.hasSuccessfulTileLoad, true);
});

test('does not fallback before initial error threshold is reached', () => {
  const state = createTileFallbackState();

  const first = noteTileError(state, { initialErrorThreshold: 2 });

  assert.equal(first, false);
  assert.equal(state.consecutiveInitialErrors, 1);
});

test('falls back when initial tile errors reach the configured threshold', () => {
  const state = createTileFallbackState();

  noteTileError(state, { initialErrorThreshold: 2 });
  const second = noteTileError(state, { initialErrorThreshold: 2 });

  assert.equal(second, true);
  assert.equal(state.consecutiveInitialErrors, 2);
});
