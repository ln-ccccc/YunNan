import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const mapDashboardSource = fs.readFileSync(new URL('../src/components/MapDashboard.vue', import.meta.url), 'utf8');
const mapContainerSource = fs.readFileSync(new URL('../src/components/MapContainer.vue', import.meta.url), 'utf8');
const leftSidebarSource = fs.readFileSync(new URL('../src/components/LeftSidebar.vue', import.meta.url), 'utf8');
const rightSidebarSource = fs.readFileSync(new URL('../src/components/RightSidebar.vue', import.meta.url), 'utf8');

// 2026-09-22 实测缺陷：地图 manifest 未就绪时 v-else 占位 div 无任何样式，
// 在 flex 行内收缩为文字宽度，数据概览/分析统计两栏直接贴合，右侧留白。
// 守护三处布局不变量，防止回归。

test('MapDashboard 根容器以 100% 撑满 AppShell 父容器而非 100vw 视口', () => {
  assert.doesNotMatch(
    mapDashboardSource,
    /\.dashboard\s*\{[^}]*width:\s*100vw/,
    '.dashboard 使用 100vw 会溢出到应用侧栏之下（旧全屏版残留）',
  );
  assert.doesNotMatch(
    mapDashboardSource,
    /\.dashboard\s*\{[^}]*height:\s*100vh/,
    '.dashboard 使用 100vh 同属旧全屏残留，应改为 100%',
  );
});

test('地图占位符 .map-unavailable 必须以 flex:1 占满中段', () => {
  assert.match(
    mapDashboardSource,
    /\.map-unavailable\s*\{[^}]*flex:\s*1/,
    '占位符缺 flex:1 时地图未就绪会让左右两栏贴合（必须保留）',
  );
});

test('左右侧栏禁止被 flex 压缩（flex-shrink: 0）', () => {
  assert.match(
    leftSidebarSource,
    /\.sidebar\s*\{[^}]*flex-shrink:\s*0/,
    'LeftSidebar .sidebar 需保持 flex-shrink: 0，否则窄视口下面板被压缩',
  );
  assert.match(
    rightSidebarSource,
    /\.sidebar\s*\{[^}]*flex-shrink:\s*0/,
    'RightSidebar .sidebar 需保持 flex-shrink: 0',
  );
});

// 2026-09-22 实测 P0：defineExpose 的对象字面量在 setup 期立即求值，
// 引用尚未声明的 const 函数会抛 TDZ ReferenceError，MapContainer 整体不挂载，
// 地图区塌成 <!---->，左右两栏贴合。守护声明顺序。
test('MapContainer defineExpose 必须位于全部被暴露函数声明之后', () => {
  const exposeIdx = mapContainerSource.indexOf('defineExpose({');
  assert.ok(exposeIdx > 0, 'MapContainer 必须保留 defineExpose');
  for (const name of ['flyToMine', 'fitMineLayerBounds', 'invalidateSize', 'toggleGraticule']) {
    const declIdx = mapContainerSource.indexOf(`const ${name} =`);
    assert.ok(
      declIdx > 0 && declIdx < exposeIdx,
      `const ${name} 声明必须出现在 defineExpose 之前（当前声明位置 ${declIdx}，expose 位置 ${exposeIdx}）`,
    );
  }
});
