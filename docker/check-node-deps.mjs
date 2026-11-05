// 启动前依赖预检（2026-11-05 迁移审查 P1-5 修正配套）
// 用法: node check-node-deps.mjs <项目根目录>
// 背景: 容器以"镜像基座 node_modules + bind 主树源码 + 启动时 npm run build"运行，
// 主树 package.json 新增依赖而镜像未同步时，构建失败点在 webpack 深处、报错晦涩。
// 本脚本把缺包在启动第一阶段点名，报错即可执行（主树 npm install 或更新镜像）。
// 注意用 require.resolve（子路径 require('pkg/package.json') 会被包的 exports 字段
// 拒绝产生假阳性，2026-11-05 实测踩过）。
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(process.argv[2] || '');
if (!fs.existsSync(path.join(root, 'package.json'))) {
  console.error(`[check-node-deps] 用法: node check-node-deps.mjs <含 package.json 的项目根目录>`);
  process.exit(2);
}

const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
const wanted = { ...pkg.dependencies, ...pkg.devDependencies };
const requireFromRoot = createRequire(path.join(root, 'noop.js'));

const missing = [];
for (const name of Object.keys(wanted)) {
  try {
    requireFromRoot.resolve(name);
  } catch {
    missing.push(name);
  }
}

if (missing.length > 0) {
  console.error(
    `[check-node-deps] ${root} 缺 ${missing.length} 个依赖（package.json 声明但 node_modules 未安装）:\n  ${missing.join('\n  ')}\n` +
    `主树执行 npm install 后重启容器，或将依赖固化进镜像基座。`,
  );
  process.exit(1);
}
console.log(`[check-node-deps] ${root}: ${Object.keys(wanted).length} 项依赖全部就绪`);
