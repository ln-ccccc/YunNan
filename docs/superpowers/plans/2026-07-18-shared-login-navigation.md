# Miner Shared Login Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让管理员在 Miner 登录一次后，点击“解译平台”直接进入主系统 `/segmentation`，并复用同一会话 Cookie。

**Architecture:** 将 URL 生成抽成两个纯函数。Miner 跳转函数保留配置端口但统一为当前浏览器主机名和 history 路由；主系统后端地址函数使用当前页面协议与主机名，仅从环境读取后端端口。服务端认证逻辑保持不变。

**Tech Stack:** Vue 3、Vue CLI、Vite、Node.js `node:test`、Docker Compose

---

### Task 1: Miner 解译平台跳转地址

**Files:**
- Create: `miner/src/navigation/geoviewNavigation.js`
- Create: `miner/test/geoviewNavigation.test.js`
- Modify: `miner/src/components/TheHeader.vue`

- [ ] **Step 1: 写失败测试**

创建 `miner/test/geoviewNavigation.test.js`：

```js
import test from 'node:test';
import assert from 'node:assert/strict';

import { buildGeoViewUrl } from '../src/navigation/geoviewNavigation.js';

test('buildGeoViewUrl reuses the current hostname and opens the history route', () => {
  const result = buildGeoViewUrl(
    'http://localhost:3000/#/segmentation',
    {
      href: 'http://192.168.1.20:4000/#/projects',
      hostname: '192.168.1.20',
    },
  );

  assert.equal(result, 'http://192.168.1.20:3000/segmentation');
});
```

- [ ] **Step 2: 验证测试按预期失败**

Run: `node --test test/geoviewNavigation.test.js`（工作目录：`miner`）

Expected: FAIL，提示找不到 `geoviewNavigation.js`。

- [ ] **Step 3: 实现最小 URL 生成函数**

创建 `miner/src/navigation/geoviewNavigation.js`：

```js
export function buildGeoViewUrl(configuredUrl, currentLocation) {
  const target = new URL(configuredUrl, currentLocation.href);
  target.hostname = currentLocation.hostname;
  target.pathname = '/segmentation';
  target.search = '';
  target.hash = '';
  return target.toString();
}
```

在 `miner/src/components/TheHeader.vue` 中导入函数，并把按钮处理改为：

```js
import { buildGeoViewUrl } from '../navigation/geoviewNavigation.js';

const goToGeoView = () => {
  const configuredUrl = import.meta.env.VITE_GEOVIEW_URL || 'http://localhost:3000/segmentation';
  window.location.href = buildGeoViewUrl(configuredUrl, window.location);
};
```

- [ ] **Step 4: 验证 Miner 测试通过**

Run: `npm test`（工作目录：`miner`）

Expected: 全部测试通过，失败数为 0。

### Task 2: 主系统后端地址与浏览器主机名统一

**Files:**
- Create: `frontend/src/utils/backendUrl.mjs`
- Create: `frontend/test/backendUrl.test.mjs`
- Modify: `frontend/src/global.vue`

- [ ] **Step 1: 写失败测试**

创建 `frontend/test/backendUrl.test.mjs`：

```js
import test from 'node:test';
import assert from 'node:assert/strict';

import { buildBackendBaseUrl } from '../src/utils/backendUrl.mjs';

test('buildBackendBaseUrl uses the current browser hostname', () => {
  assert.equal(
    buildBackendBaseUrl({ protocol: 'http:', hostname: 'localhost', port: 5008 }),
    'http://localhost:5008/',
  );
  assert.equal(
    buildBackendBaseUrl({ protocol: 'http:', hostname: '192.168.1.20', port: 5008 }),
    'http://192.168.1.20:5008/',
  );
});
```

- [ ] **Step 2: 验证测试按预期失败**

Run: `node --test test/backendUrl.test.mjs`（工作目录：`frontend`）

Expected: FAIL，提示找不到 `backendUrl.mjs`。

- [ ] **Step 3: 实现最小后端地址生成函数**

创建 `frontend/src/utils/backendUrl.mjs`：

```js
export function buildBackendBaseUrl({ protocol, hostname, port }) {
  return `${protocol}//${hostname}:${port}/`;
}
```

将 `frontend/src/global.vue` 改为：

```js
import { buildBackendBaseUrl } from '@/utils/backendUrl.mjs';

const BASEURL = buildBackendBaseUrl({
  protocol: window.location.protocol,
  hostname: window.location.hostname,
  port: process.env.VUE_APP_BACKEND_PORT,
});

export default {
  BASEURL,
};
```

- [ ] **Step 4: 验证主系统测试通过**

Run: `node --test test/backendUrl.test.mjs`（工作目录：`frontend`）

Expected: 1 个测试通过，失败数为 0。

### Task 3: 构建和真实会话验证

**Files:**
- Verify only: `miner/dist/**`
- Verify only: `frontend/dist/**`

- [ ] **Step 1: 构建两个前端**

Run: `npm run build`（工作目录：`miner`）

Run: `npm run build`（工作目录：`frontend`）

Expected: 两个命令退出码均为 0。

- [ ] **Step 2: 重建前端容器**

使用当前 MySQL 容器中的数据库密码注入 Compose 进程，执行：

```powershell
$env:INFERENCE_IMAGE = 'geoview-inference-worker:current'
docker compose --env-file .env --env-file image_bundle.env `
  -f docker-compose.prod.yml -f docker-compose.gpu.yml `
  up -d --force-recreate --no-deps frontend miner-web
```

Expected: `cugrs-frontend` 与 `cugrs-miner-web` 状态为 running/healthy。

- [ ] **Step 3: 验证共享会话**

在同一个 Cookie 会话中：

1. POST `http://localhost:4000/api/auth/login`。
2. GET `http://localhost:5008/api/auth/session`。
3. 确认 `data.authenticated` 为 `true`。
4. GET `http://localhost:3000/segmentation`，确认 HTTP 200。

- [ ] **Step 4: 验证跳转和退出保护**

在浏览器中从 `http://localhost:4000/#/projects` 点击“解译平台”，确认地址为 `http://localhost:3000/segmentation` 且不显示登录页。随后退出登录，重新访问 `/segmentation`，确认跳转到 `/login?redirect=...`。

### Task 4: 交付核验

**Files:**
- Verify: `docs/superpowers/specs/2026-07-18-shared-login-navigation-design.md`
- Verify: files listed above

- [ ] **Step 1: 核对修改范围**

确认只修改两个 URL 生成点、两个纯函数、两个测试文件和本设计/计划文档；不修改后端认证、密码或数据库。

- [ ] **Step 2: 最终验证**

Run: `npm test`（工作目录：`miner`）

Run: `node --test test/backendUrl.test.mjs`（工作目录：`frontend`）

Run: `npm run build`（分别在 `miner`、`frontend`）

Expected: 测试失败数为 0，两个构建退出码为 0。

> 当前工作区没有可用 Git 元数据，因此不包含提交步骤；每个 Task 结束后以测试结果作为可回滚检查点。
