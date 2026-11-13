// 启动前依赖预检（2026-11-05 迁移审查 P1-5 修正配套）
// 用法: node check-node-deps.mjs <项目根目录>
// 背景: 容器以"镜像基座 node_modules + bind 主树源码 + 启动时 npm run build"运行，
// 主树 package.json 新增依赖而镜像未同步时，构建失败点在 webpack 深处、报错晦涩。
// 本脚本把缺包在启动第一阶段点名，报错即可执行（主树 npm install 或更新镜像）。
// 注意用 require.resolve（子路径 require('pkg/package.json') 会被包的 exports 字段
// 拒绝产生假阳性，2026-11-05 实测踩过）。
//
// 锁文件版本校验（2026-11-14 审查问题30）：只验证"包能否解析"发现不了
// node_modules 与 package-lock.json 的版本漂移——镜像基座停留在构建时点而主树
// 已升级时，启动构建产物会偏离交付包预期版本。构建输入必须与锁文件同源。
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

const lockPath = path.join(root, 'package-lock.json');
if (!fs.existsSync(lockPath)) {
  console.error(
    `[check-node-deps] ${root} 缺少 package-lock.json —— 构建输入必须与锁文件同源，` +
      `请在主树提交锁文件后重启容器（2026-11-14 审查问题30）。`,
  );
  process.exit(1);
}
const lock = JSON.parse(fs.readFileSync(lockPath, 'utf8'));

function lockedVersion(name) {
  const entry = (lock.packages || {})[`node_modules/${name}`] || (lock.dependencies || {})[name];
  return entry ? entry.version : null;
}

// 从 require.resolve 的入口文件路径向上找最近 package.json 直接 fs 读取——
// 绕过 exports 字段对子路径 require 的限制，拿已安装包的真实版本
function installedVersion(name) {
  let dir = path.dirname(requireFromRoot.resolve(name));
  for (;;) {
    const candidate = path.join(dir, 'package.json');
    if (fs.existsSync(candidate)) {
      try {
        return JSON.parse(fs.readFileSync(candidate, 'utf8')).version || null;
      } catch {
        return null;
      }
    }
    const parent = path.dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

const missing = [];
const drift = [];
for (const name of Object.keys(wanted)) {
  try {
    requireFromRoot.resolve(name);
  } catch {
    missing.push(name);
    continue;
  }
  const expected = lockedVersion(name);
  if (!expected) continue; // 锁文件未收录（optional/平台特定包）：存在性检查已覆盖
  const actual = installedVersion(name);
  if (actual && actual !== expected) {
    drift.push(`  ${name}: 锁文件 ${expected} ≠ 已安装 ${actual}`);
  }
}

if (missing.length > 0 || drift.length > 0) {
  if (missing.length > 0) {
    console.error(
      `[check-node-deps] ${root} 缺 ${missing.length} 个依赖（package.json 声明但 node_modules 未安装）:\n  ${missing.join('\n  ')}\n` +
        `主树执行 npm install 后重启容器，或将依赖固化进镜像基座。`,
    );
  }
  if (drift.length > 0) {
    console.error(
      `[check-node-deps] ${root} 有 ${drift.length} 个依赖与 package-lock.json 版本不一致:\n${drift.join('\n')}\n` +
        `主树执行 npm ci 后重启容器，或重建镜像基座——构建输入必须与锁文件同源。`,
    );
  }
  process.exit(1);
}
console.log(`[check-node-deps] ${root}: ${Object.keys(wanted).length} 项依赖全部就绪且与锁文件版本一致`);
