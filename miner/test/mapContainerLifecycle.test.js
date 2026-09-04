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
