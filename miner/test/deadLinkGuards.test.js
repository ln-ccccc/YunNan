import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

// 守护断言：spectral_live 端点级死链不得回潮。
// /api/geoview/spectral_live/:fid 代理指向的 Flask 路由 /api/analysis/spectral_live
// 在后端从未实现（backend 全仓零实现），每次请求必然 404 且被 catch 静默吞掉，
// 已于 2026-09-15 审计批次 Y2-3 删除（routes/geoview.js、services/geoviewBackend.js
// 整文件与 server.js 挂载）。本测试递归扫描 miner 源码，防止死链被重新引入。

const minerRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SCAN_DIRS = ['routes', 'services', 'src'];
const FORBIDDEN = ['spectral_live', 'fetchSpectralLive'];

function collectSourceFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...collectSourceFiles(full));
    } else if (/\.(js|vue)$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

test('miner 源码不再引用已删除的 spectral_live 死链', () => {
  const files = SCAN_DIRS.flatMap((dir) => {
    const full = path.join(minerRoot, dir);
    return fs.existsSync(full) ? collectSourceFiles(full) : [];
  });
  files.push(path.join(minerRoot, 'server.js'));
  assert.ok(files.length > 0);

  for (const file of files) {
    const text = fs.readFileSync(file, 'utf-8');
    for (const token of FORBIDDEN) {
      assert.ok(
        !text.includes(token),
        `${path.relative(minerRoot, file)} 不应再引用已删除的死链 ${token}`,
      );
    }
  }
});

test('server.js 不再挂载 /api/geoview 代理路由', () => {
  const server = fs.readFileSync(path.join(minerRoot, 'server.js'), 'utf-8');
  assert.ok(!server.includes("routes/geoview.js"));
  assert.ok(!server.includes("app.use('/api/geoview'"));
});
