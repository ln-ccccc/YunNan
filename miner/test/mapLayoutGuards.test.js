import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const mapDashboardSource = fs.readFileSync(new URL('../src/components/MapDashboard.vue', import.meta.url), 'utf8');
const mapContainerSource = fs.readFileSync(new URL('../src/components/MapContainer.vue', import.meta.url), 'utf8');
const leftSidebarSource = fs.readFileSync(new URL('../src/components/LeftSidebar.vue', import.meta.url), 'utf8');
const rightSidebarSource = fs.readFileSync(new URL('../src/components/RightSidebar.vue', import.meta.url), 'utf8');
const modulePlaceholderSource = fs.readFileSync(new URL('../src/components/ModulePlaceholder.vue', import.meta.url), 'utf8');
const baseCssSource = fs.readFileSync(new URL('../src/assets/base.css', import.meta.url), 'utf8');

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

// 2026-09-25 实测缺陷：占位页在侧栏（200px）右侧再溢出 200px（placeholder-view
// 右缘 2120 / 视口 1920），内容区底部出现横向滚动条——与 .dashboard 同源的 100vw 残留。
test('ModulePlaceholder 占位页以 100% 撑满内容区而非 100vw 视口', () => {
  assert.doesNotMatch(
    modulePlaceholderSource,
    /\.placeholder-view\s*\{[^}]*width:\s*100vw/,
    '.placeholder-view 使用 100vw 会溢出侧栏宽度（应改为 100%）',
  );
  assert.doesNotMatch(
    modulePlaceholderSource,
    /\.placeholder-view\s*\{[^}]*height:\s*100vh/,
    '.placeholder-view 使用 100vh 同属旧全屏残留，应改为 100%',
  );
});

// 2026-09-25 实测缺陷：base.css 保留 Vue 脚手架的 prefers-color-scheme: dark 块，
// 宿主系统深色模式时 body 翻成 #181818、文字翻成浅色——影像/搜索/设置等透明根视图
// 黑底透出，深绿标题（#264b45）对比度不足 2:1 不可读。应用为固定浅色主题（地图页
// 自带深色配色不受影响），脚手架深色块必须移除。
test('base.css 禁止保留脚手架 prefers-color-scheme 深色块', () => {
  assert.doesNotMatch(
    baseCssSource,
    /prefers-color-scheme/,
    'base.css 的深色方案媒体查询会让宿主深色模式下 body 变黑，浅色主题视图全部透黑（必须移除）',
  );
});
