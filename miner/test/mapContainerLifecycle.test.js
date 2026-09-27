import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const mapContainerSource = fs.readFileSync(new URL('../src/components/MapContainer.vue', import.meta.url), 'utf8');

test('renders already-loaded mine features when the map container mounts', () => {
  assert.match(
    mapContainerSource,
    /onMounted\(\(\) => \{\s*initMap\(\);\s*renderMapMarkers\(\);/s,
    'MapContainer must render the initial minesData after initializing Leaflet',
  );
});

test('mine layer uses canvas renderer with hit tolerance for tiny polygons', () => {
  // 矿山多边形省级视野下仅 ~2px（gui-audit #3），必须带命中容差，否则点击不可达
  assert.match(
    mapContainerSource,
    /L\.canvas\(\{\s*padding:[^,]+,\s*tolerance:\s*\d+\s*\}\)/,
    'mineLayer must be rendered with L.canvas tolerance for click hit-testing',
  );
  assert.match(
    mapContainerSource,
    /renderer:\s*mineRenderer/,
    'geoJSON layer must consume the canvas renderer',
  );
});
