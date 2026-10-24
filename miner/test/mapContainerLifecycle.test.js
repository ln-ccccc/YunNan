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

test('mine tooltip binds a DOM element instead of raw name string', () => {
  // 矿山名来自第三方导入，Leaflet 对字符串按 HTML 渲染；bindTooltip 直接传
  // name 会让 <img onerror> 类恶意名称在悬停时执行（2026-10-24 审查 P1-5）
  assert.doesNotMatch(
    mapContainerSource,
    /bindTooltip\(name/,
    'bindTooltip must not receive the raw name string',
  );
  assert.match(
    mapContainerSource,
    /textContent\s*=\s*name/,
    'tooltip content must flow through textContent',
  );
});
