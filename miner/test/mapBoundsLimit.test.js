import assert from 'node:assert/strict';
import test from 'node:test';

import { projectBoundsLimit } from '../src/utils/mapBoundsLimit.js';

test('projectBoundsLimit pads a valid manifest bounds', () => {
  const limit = projectBoundsLimit([100.76, 26.12, 100.775, 26.135]);
  assert.ok(limit.west < 100.76 && limit.east > 100.775);
  assert.ok(limit.south < 26.12 && limit.north > 26.135);
  // 极小跨度触发最小绝对外扩，避免与 fitBounds 视口重合锁死地图
  assert.ok(limit.east - limit.west >= 0.04);
  assert.ok(limit.north - limit.south >= 0.04);
});

test('projectBoundsLimit unions mine bounds extending beyond basemap', () => {
  const limit = projectBoundsLimit(
    [100.76, 26.12, 100.775, 26.135],
    { west: 100.5, south: 26.0, east: 100.9, north: 26.2 }
  );
  assert.ok(limit.west < 100.5);
  assert.ok(limit.east > 100.9);
  assert.ok(limit.south < 26.0);
  assert.ok(limit.north > 26.2);
});

test('projectBoundsLimit keeps mine bounds fully inside basemap bounds', () => {
  const limit = projectBoundsLimit(
    [98.86, 24.66, 101.04, 26.70],
    { west: 99.06, south: 24.81, east: 100.87, north: 26.64 }
  );
  assert.ok(limit.west < 98.86 && limit.east > 101.04);
  assert.ok(limit.south < 24.66 && limit.north > 26.70);
});

test('projectBoundsLimit rejects invalid or missing basemap bounds', () => {
  assert.equal(projectBoundsLimit(null), null);
  assert.equal(projectBoundsLimit(undefined), null);
  assert.equal(projectBoundsLimit([1, 2, 3]), null);
  assert.equal(projectBoundsLimit(['a', 'b', 'c', 'd']), null);
  assert.equal(projectBoundsLimit([101, 26.1, 100, 26.2]), null); // west >= east
  assert.equal(projectBoundsLimit([100, 26.1, 101, 26]), null); // south >= north
  assert.equal(projectBoundsLimit([100, -91, 101, 26]), null); // 纬度越界
});

test('projectBoundsLimit tolerates invalid mine bounds without losing basemap limit', () => {
  const limit = projectBoundsLimit([100.76, 26.12, 100.775, 26.135], { west: NaN, south: 26, east: 100.9, north: 26.2 });
  assert.ok(limit);
  assert.ok(limit.west < 100.76);
});
