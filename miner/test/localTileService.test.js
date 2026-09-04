import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { buildEmptyTilePng, resolveLocalTilePath } from '../services/localTileService.js';

test('resolveLocalTilePath returns the png path for an existing tile', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tile-root-'));
  const tileDir = path.join(root, '10', '797');
  fs.mkdirSync(tileDir, { recursive: true });
  const tilePath = path.join(tileDir, '433.png');
  fs.writeFileSync(tilePath, Buffer.from('png'));

  const resolved = resolveLocalTilePath(root, 10, 797, 433);

  assert.equal(resolved, tilePath);
});

test('resolveLocalTilePath returns null when the requested tile is missing', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tile-root-'));

  const resolved = resolveLocalTilePath(root, 10, 797, 433);

  assert.equal(resolved, null);
});

test('buildEmptyTilePng returns a non-empty png buffer', () => {
  const png = buildEmptyTilePng();

  assert.equal(Buffer.isBuffer(png), true);
  assert.equal(png.length > 0, true);
  assert.equal(png.subarray(1, 4).toString('ascii'), 'PNG');
});
