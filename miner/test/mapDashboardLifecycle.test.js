import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const mapDashboardSource = fs.readFileSync(new URL('../src/components/MapDashboard.vue', import.meta.url), 'utf8');

test('MapDashboard removes its window resize listener on unmount', () => {
  assert.match(
    mapDashboardSource,
    /onMounted\(\(\) => \{[\s\S]*?resizeListener\.attach\(\);[\s\S]*?\}\);/,
    'MapDashboard must attach the shared resize listener in onMounted',
  );
  assert.match(
    mapDashboardSource,
    /onUnmounted\(\(\) => \{\s*resizeListener\.detach\(\);\s*\}\);/,
    'MapDashboard must detach the resize listener in onUnmounted',
  );
  assert.doesNotMatch(
    mapDashboardSource,
    /window\.addEventListener\('resize'/,
    'MapDashboard must not register anonymous resize listeners directly on window',
  );
});
