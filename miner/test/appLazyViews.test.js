import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

// 源码形状守护（2026-11-14 审查问题34）：hash 导航型 App 没有路由级
// code splitting 机会，主要视图必须经 defineAsyncComponent 动态 import，
// 防止回退为静态 import 把全部页面打进首屏 chunk（沿 mapLayoutGuards 惯例）。
const source = fs.readFileSync(
  fileURLToPath(new URL('../src/App.vue', import.meta.url)),
  'utf8',
);

const LAZY_VIEWS = [
  'ProjectWorkspace',
  'EditingView',
  'ImageryView',
  'SearchView',
  'SmartInterpretationView',
  'DataManagerView',
  'SettingsView',
];

test('主要视图经 defineAsyncComponent 动态加载', () => {
  for (const name of LAZY_VIEWS) {
    assert.ok(
      new RegExp(`const ${name} = defineAsyncComponent\\(\\(\\) => import\\(`).test(source),
      `${name} 必须以 defineAsyncComponent 异步加载`,
    );
    assert.ok(
      !new RegExp(`^import ${name} from`, 'm').test(source),
      `${name} 不得回退为静态 import`,
    );
  }
});

test('登录页与地图主控台保持静态导入', () => {
  assert.ok(/^import LoginPage from/m.test(source), 'LoginPage 是首屏，保持静态导入');
  assert.ok(
    /^import MapDashboard from/m.test(source),
    'MapDashboard 保持静态导入：openMapView 在 nextTick 后立即调用其 ref 方法，'
      + '异步包装在首次进入时 chunk 未就绪会静默丢失 focusByFid/openInferenceModal 调用；'
      + '改为异步前必须先解决该时序',
  );
});
