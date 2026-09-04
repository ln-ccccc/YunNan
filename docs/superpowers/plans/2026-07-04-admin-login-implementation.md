# 单管理员全站登录 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 YunNan 当前 `backend + miner` 系统增加单管理员 `admin` 的全站登录、未登录接口拦截、Docker 环境变量初始化与密码重置能力，并在完成后做全面测试和本次改动范围代码 review。

**Architecture:** 认证由 Flask `backend` 统一负责，密码哈希和服务端会话都存放在 `backend`。浏览器只访问 `miner` 的 `4000` 端口，`miner/server.js` 作为认证代理转发登录/登出/会话检查，并在本地业务 API 前增加登录校验；前端在 `App.vue` 层引入登录壳与路由门禁。

**Tech Stack:** Flask, SQLAlchemy, Werkzeug password hashing, Express, Node fetch, Vue 3, Docker Compose, Node test runner, Python unittest

---

## File Map

- Create: `backend/applications/models/admin_user.py`
- Create: `backend/applications/auth/service.py`
- Create: `backend/applications/auth/guard.py`
- Create: `backend/applications/api/auth.py`
- Create: `backend/test_auth_api.py`
- Create: `miner/services/authBackend.js`
- Create: `miner/services/authProxy.js`
- Create: `miner/src/auth/sessionClient.js`
- Create: `miner/src/auth/authGuards.js`
- Create: `miner/src/components/LoginPage.vue`
- Create: `miner/test/authProxy.test.js`
- Create: `miner/test/authGuards.test.js`
- Modify: `backend/applications/models/__init__.py`
- Modify: `backend/applications/api/__init__.py`
- Modify: `backend/applications/configs/config.py`
- Modify: `backend/applications/common/scripts/__init__.py`
- Modify: `backend/applications/__init__.py`
- Modify: `backend/test_project_api.py`
- Modify: `docker-compose.prod.yml`
- Modify: `docs/project_summary.md`
- Modify: `docs/development_guide.md`
- Modify: `miner/server.js`
- Modify: `miner/services/projectBackend.js`
- Modify: `miner/routes/projects.js`
- Modify: `miner/src/App.vue`
- Modify: `miner/src/components/TheHeader.vue`

### Task 1: 后端管理员模型与初始化

**Files:**
- Create: `backend/applications/models/admin_user.py`
- Modify: `backend/applications/models/__init__.py`
- Modify: `backend/applications/configs/config.py`
- Modify: `backend/applications/common/scripts/__init__.py`
- Test: `backend/test_auth_api.py`

- [ ] **Step 1: 先写管理员初始化测试**

```python
def test_bootstrap_admin_from_environment(self):
    os.environ["ADMIN_USERNAME"] = "admin"
    os.environ["ADMIN_PASSWORD"] = "Secret123!"

    from applications.auth.service import sync_admin_from_env, verify_admin_password

    user = sync_admin_from_env()

    self.assertEqual(user.username, "admin")
    self.assertTrue(verify_admin_password("admin", "Secret123!"))
```

- [ ] **Step 2: 跑单测，确认当前失败**

Run: `python -m unittest backend.test_auth_api.TestAuthAPI.test_bootstrap_admin_from_environment`

Expected: `ImportError` 或 `AttributeError`，因为认证服务和管理员模型尚不存在。

- [ ] **Step 3: 增加管理员模型与环境变量配置**

```python
# backend/applications/models/admin_user.py
import datetime

from applications.extensions import db


class AdminUser(db.Model):
    __tablename__ = "admin_user"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(64), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    last_login_at = db.Column(db.DateTime)
    create_time = db.Column(db.DateTime, default=datetime.datetime.now, nullable=False)
    update_time = db.Column(
        db.DateTime,
        default=datetime.datetime.now,
        onupdate=datetime.datetime.now,
        nullable=False,
    )
```

```python
# backend/applications/configs/config.py
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
```

- [ ] **Step 4: 实现最小初始化服务**

```python
# backend/applications/auth/service.py
from werkzeug.security import check_password_hash, generate_password_hash

from applications.extensions import db
from applications.models import AdminUser


def sync_admin_from_env():
    username = current_app.config["ADMIN_USERNAME"]
    password = current_app.config["ADMIN_PASSWORD"]
    if not password:
        raise RuntimeError("ADMIN_PASSWORD is required")
    user = AdminUser.query.filter_by(username=username).first()
    if user is None:
        user = AdminUser(username=username, password_hash=generate_password_hash(password))
        db.session.add(user)
    else:
        user.password_hash = generate_password_hash(password)
        user.is_active = True
    db.session.commit()
    return user


def verify_admin_password(username, password):
    user = AdminUser.query.filter_by(username=username, is_active=True).first()
    return bool(user and check_password_hash(user.password_hash, password))
```

- [ ] **Step 5: 再跑单测，确认通过**

Run: `python -m unittest backend.test_auth_api.TestAuthAPI.test_bootstrap_admin_from_environment`

Expected: `OK`

- [ ] **Step 6: 提交本任务**

```bash
git add backend/applications/models/admin_user.py backend/applications/models/__init__.py backend/applications/configs/config.py backend/applications/common/scripts/__init__.py backend/applications/auth/service.py backend/test_auth_api.py
git commit -m "feat: add admin bootstrap model and service"
```

### Task 2: 后端登录接口、会话与受保护接口门禁

**Files:**
- Create: `backend/applications/auth/guard.py`
- Create: `backend/applications/api/auth.py`
- Modify: `backend/applications/api/__init__.py`
- Modify: `backend/applications/__init__.py`
- Modify: `backend/applications/api/project.py`
- Modify: `backend/test_project_api.py`
- Test: `backend/test_auth_api.py`

- [ ] **Step 1: 先写登录与 401 测试**

```python
def test_login_logout_and_session_guard(self):
    self.sync_admin("Secret123!")

    unauthorized = self.client.get("/api/projects")
    self.assertEqual(unauthorized.status_code, 401)

    login = self.client.post("/api/auth/login", json={"username": "admin", "password": "Secret123!"})
    self.assertEqual(login.status_code, 200)

    session_state = self.client.get("/api/auth/session")
    self.assertTrue(self._json(session_state)["data"]["authenticated"])

    logout = self.client.post("/api/auth/logout")
    self.assertEqual(logout.status_code, 200)

    after_logout = self.client.get("/api/projects")
    self.assertEqual(after_logout.status_code, 401)
```

- [ ] **Step 2: 运行测试，确认因为接口不存在或未拦截而失败**

Run: `python -m unittest backend.test_auth_api.TestAuthAPI.test_login_logout_and_session_guard`

Expected: `/api/auth/login` 404 或 `/api/projects` 错误地返回 200。

- [ ] **Step 3: 实现认证 guard 与接口**

```python
# backend/applications/auth/guard.py
from functools import wraps
from flask import session


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_user_id"):
            return jsonify(success=False, code=401, msg="未登录"), 401
        return view(*args, **kwargs)
    return wrapped
```

```python
# backend/applications/api/auth.py
auth_api = Blueprint("auth_api", __name__, url_prefix="/api/auth")


@auth_api.post("/login")
def login_api():
    payload = request.json or {}
    if not authenticate(payload.get("username"), payload.get("password")):
        return jsonify(success=False, code=1, msg="账号或密码错误"), 401
    session.clear()
    session["admin_user_id"] = user.id
    session["admin_username"] = user.username
    return success_api(data={"authenticated": True, "username": user.username})


@auth_api.get("/session")
def session_api():
    return success_api(data={
        "authenticated": bool(session.get("admin_user_id")),
        "username": session.get("admin_username"),
    })


@auth_api.post("/logout")
def logout_api():
    session.clear()
    return success_api(msg="已退出")
```

- [ ] **Step 4: 把项目接口挂上统一登录门禁**

```python
@project_api.get("")
@login_required
def project_list_api():
    ...
```

同样给 `project.py` 中所有增删改查接口加 `@login_required`。

- [ ] **Step 5: 修正现有项目 API 测试，让它先登录再跑原流程**

```python
def login_as_admin(self, password="Secret123!"):
    self.sync_admin(password)
    response = self.client.post("/api/auth/login", json={"username": "admin", "password": password})
    self.assertEqual(response.status_code, 200)
```

- [ ] **Step 6: 运行后端认证与项目测试**

Run: `python -m unittest backend.test_auth_api backend.test_project_api`

Expected: 全部 `OK`

- [ ] **Step 7: 提交本任务**

```bash
git add backend/applications/auth/guard.py backend/applications/api/auth.py backend/applications/api/__init__.py backend/applications/__init__.py backend/applications/api/project.py backend/test_auth_api.py backend/test_project_api.py
git commit -m "feat: add backend auth API and project guards"
```

### Task 3: Miner Node 代理登录态与业务 API 拦截

**Files:**
- Create: `miner/services/authBackend.js`
- Create: `miner/services/authProxy.js`
- Create: `miner/test/authProxy.test.js`
- Modify: `miner/server.js`
- Modify: `miner/services/projectBackend.js`
- Modify: `miner/routes/projects.js`

- [ ] **Step 1: 先写 Node 侧 cookie 转发与 401 门禁测试**

```javascript
test('relayBackendResponse forwards set-cookie and status', async () => {
  const upstream = {
    status: 200,
    body: { success: true, data: { authenticated: true, username: 'admin' } },
    setCookies: ['session=abc; HttpOnly; Path=/'],
  };
  const res = mockResponse();

  relayBackendResponse(res, upstream);

  assert.deepEqual(res.cookies, upstream.setCookies);
  assert.equal(res.statusCode, 200);
});

test('requireMinerAuth returns 401 when backend session is invalid', async () => {
  const req = { headers: { cookie: '' } };
  const res = mockResponse();
  await requireMinerAuth({ sessionApi: async () => ({ status: 401, body: { success: false } }) })(req, res, () => {});
  assert.equal(res.statusCode, 401);
});
```

- [ ] **Step 2: 运行测试，确认当前失败**

Run: `npm test -- test/authProxy.test.js`

Expected: 找不到 `authProxy.js` 或导出的函数。

- [ ] **Step 3: 实现 backend 认证代理客户端**

```javascript
// miner/services/authBackend.js
export async function requestBackendAuth(method, path, { body, cookie } = {}) {
  const response = await fetch(`${backendBaseUrl}${path}`, {
    method,
    headers: {
      ...(body ? { 'content-type': 'application/json' } : {}),
      ...(cookie ? { cookie } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const parsed = await response.json();
  return {
    status: response.status,
    body: parsed,
    setCookies: typeof response.headers.getSetCookie === 'function' ? response.headers.getSetCookie() : [],
  };
}
```

- [ ] **Step 4: 实现 miner 认证中间件与响应转发**

```javascript
// miner/services/authProxy.js
export function relayBackendResponse(res, upstream) {
  for (const cookie of upstream.setCookies || []) {
    res.append('set-cookie', cookie);
  }
  res.status(upstream.status || 200).json(upstream.body);
}

export function requireMinerAuth({ sessionApi }) {
  return async (req, res, next) => {
    const upstream = await sessionApi(req.headers.cookie || '');
    if (upstream.status !== 200 || !upstream.body?.data?.authenticated) {
      return res.status(401).json({ success: false, code: 401, msg: '未登录' });
    }
    req.auth = upstream.body.data;
    next();
  };
}
```

- [ ] **Step 5: 在 `miner/server.js` 注册 `/api/auth/*`，并给业务接口加门禁**

```javascript
app.post('/api/auth/login', async (req, res) => {
  relayBackendResponse(res, await authBackend.login(req.body || {}));
});

app.use('/api/projects', requireMinerAuth({ sessionApi: authBackend.session }), createProjectRoutes(...));
app.get('/api/stats', requireMinerAuth({ sessionApi: authBackend.session }), statsHandler);
app.get('/api/geojson', requireMinerAuth({ sessionApi: authBackend.session }), geojsonHandler);
```

- [ ] **Step 6: 更新 `projectBackend.js`，支持把浏览器 cookie 转发到 Flask**

```javascript
export async function requestJson(method, path, { query, body, cookie } = {}) {
  ...
  const response = await fetch(url, {
    method,
    headers: {
      ...(body ? { 'content-type': 'application/json' } : {}),
      ...(cookie ? { cookie } : {}),
    },
    ...
  });
}
```

- [ ] **Step 7: 运行 Node 认证与现有路由测试**

Run: `npm test -- test/authProxy.test.js test/projectRoutes.test.js test/projectExportFeatures.test.js test/viewNavigation.test.js`

Expected: 全部通过。

- [ ] **Step 8: 提交本任务**

```bash
git add miner/services/authBackend.js miner/services/authProxy.js miner/test/authProxy.test.js miner/server.js miner/services/projectBackend.js miner/routes/projects.js
git commit -m "feat: protect miner APIs with backend session auth"
```

### Task 4: 前端登录壳、登录页与路由门禁

**Files:**
- Create: `miner/src/auth/sessionClient.js`
- Create: `miner/src/auth/authGuards.js`
- Create: `miner/src/components/LoginPage.vue`
- Create: `miner/test/authGuards.test.js`
- Modify: `miner/src/App.vue`
- Modify: `miner/src/components/TheHeader.vue`

- [ ] **Step 1: 先写前端门禁纯函数测试**

```javascript
test('resolveInitialView returns login when not authenticated', () => {
  assert.equal(resolveInitialView({ authenticated: false, hash: '#/map' }), 'login');
});

test('resolveInitialView keeps requested view when authenticated', () => {
  assert.equal(resolveInitialView({ authenticated: true, hash: '#/map' }), 'map');
});
```

- [ ] **Step 2: 运行测试，确认当前失败**

Run: `npm test -- test/authGuards.test.js`

Expected: 模块不存在或断言失败。

- [ ] **Step 3: 实现最小会话客户端和纯函数门禁**

```javascript
// miner/src/auth/sessionClient.js
import axios from 'axios';

export async function fetchSession() {
  const response = await axios.get('/api/auth/session');
  return response.data?.data || { authenticated: false, username: null };
}

export async function login(username, password) {
  const response = await axios.post('/api/auth/login', { username, password });
  return response.data?.data || { authenticated: false };
}

export async function logout() {
  await axios.post('/api/auth/logout');
}
```

```javascript
// miner/src/auth/authGuards.js
import { resolveViewFromHash } from '../navigation/viewNavigation.js';

export function resolveInitialView({ authenticated, hash }) {
  if (!authenticated) return 'login';
  return resolveViewFromHash(hash);
}
```

- [ ] **Step 4: 在 `App.vue` 引入登录壳和 401 回退**

```vue
<template>
  <LoginPage v-if="currentView === 'login'" :submitting="authLoading" :error="authError" @login="handleLogin" />
  <div v-else class="app-shell">
    <ProjectWorkspace v-show="currentView === 'projects'" @open-map="openMapView" />
    <MapDashboard v-show="currentView === 'map'" ... />
  </div>
</template>
```

核心逻辑：

```javascript
onMounted(async () => {
  sessionState.value = await fetchSession();
  currentView.value = resolveInitialView({ authenticated: sessionState.value.authenticated, hash: window.location.hash });
});
```

- [ ] **Step 5: 让页头显示真实用户与退出按钮**

```vue
<div class="user-profile">
  <span class="role">{{ username || '未登录' }}</span>
</div>
<button v-if="username" class="secondary-btn" type="button" @click="$emit('logout')">
  <span>退出登录</span>
</button>
```

- [ ] **Step 6: 跑纯函数测试和前端构建**

Run: `npm test -- test/authGuards.test.js test/viewNavigation.test.js`

Expected: 全部通过。

Run: `npm run build`

Expected: `vite build` 成功。

- [ ] **Step 7: 提交本任务**

```bash
git add miner/src/auth/sessionClient.js miner/src/auth/authGuards.js miner/src/components/LoginPage.vue miner/test/authGuards.test.js miner/src/App.vue miner/src/components/TheHeader.vue
git commit -m "feat: add frontend login shell and session guards"
```

### Task 5: Docker 环境变量、文档、全面测试与代码 Review

**Files:**
- Modify: `docker-compose.prod.yml`
- Modify: `docs/project_summary.md`
- Modify: `docs/development_guide.md`
- Verification: Docker 容器与浏览器联调

- [ ] **Step 1: 先更新 Compose 环境变量声明**

```yaml
x-app-env: &app-env
  ...
  ADMIN_USERNAME: "${ADMIN_USERNAME:-admin}"
  ADMIN_PASSWORD: "${ADMIN_PASSWORD:-ChangeMe123!}"
```

- [ ] **Step 2: 更新开发与部署文档**

```markdown
- 新增单管理员登录：默认用户名固定为 `admin`
- 需要在 Docker 环境变量中提供 `ADMIN_PASSWORD`
- 忘记密码时，修改 `ADMIN_PASSWORD` 并重启容器即可重置
```

- [ ] **Step 3: 跑后端、Node 和构建验证**

Run: `python -m unittest backend.test_auth_api backend.test_project_api`

Expected: 全部 `OK`

Run: `npm test -- test/authProxy.test.js test/authGuards.test.js test/projectRoutes.test.js test/projectExportFeatures.test.js test/projectWorkspaceHelpers.test.js test/viewNavigation.test.js`

Expected: 全部通过

Run: `npm run build`

Expected: `vite build` 成功

- [ ] **Step 4: 重启 Docker 并做集成验证**

Run: `docker compose -f docker-compose.prod.yml restart backend miner-api miner-web`

Expected: 三个容器重启并恢复健康。

Run:

```bash
curl -i http://127.0.0.1:4000/api/auth/session
curl -i http://127.0.0.1:4000/api/projects
```

Expected:

- 未登录访问 `/api/auth/session` 返回 `authenticated=false`
- 未登录访问 `/api/projects` 返回 `401`

- [ ] **Step 5: 用浏览器做关键 UI 路径验证**

验证项：

- 未登录打开 `http://127.0.0.1:4000/#/projects` 只看到登录页
- 使用 `admin + ADMIN_PASSWORD` 登录成功
- 登录后进入项目工作台
- 直接访问 `#/map` 已登录可进入，未登录会被拦回登录页
- 点击“退出登录”后回到登录页
- 退出后再次请求业务接口返回 `401`

- [ ] **Step 6: 做本次改动范围代码 review**

Review 清单：

- `backend` 是否存在未加 `login_required` 的受保护业务接口
- `miner/server.js` 是否还有未挂 `requireMinerAuth` 的公开业务 API
- 登录响应是否泄露账号存在性
- 浏览器是否有 `localStorage/sessionStorage` 明文凭据
- 401 回退是否覆盖 `projects/map` 两条主路径
- Docker 文档是否说明了 `ADMIN_PASSWORD` 初始化和重置方式
- 测试是否覆盖未登录、已登录、退出登录、密码重置

- [ ] **Step 7: 汇总测试结果与 review 结论**

交付时必须明确列出：

- 改了什么
- 为什么这样改
- 实际执行的命令
- 每条命令的结果
- code review 发现与剩余风险

- [ ] **Step 8: 提交本任务**

```bash
git add docker-compose.prod.yml docs/project_summary.md docs/development_guide.md
git commit -m "docs: document admin login deployment and verification"
```

## Self-Review

- Spec coverage:
  - 单管理员 `admin`：Task 1
  - 后端统一认证与服务端会话：Task 1-2
  - 全站登录门禁：Task 2-4
  - Docker 环境变量初始化与密码重置：Task 1, Task 5
  - 全面测试：Task 5
  - 改动范围代码 review：Task 5
- Placeholder scan:
  - 无 `TBD/TODO` 占位符
  - 所有测试、命令、主要文件路径已落到具体项
- Type consistency:
  - 统一使用 `authenticated`, `username`, `admin_user_id`, `ADMIN_PASSWORD`

Plan complete and saved to `docs/superpowers/plans/2026-07-04-admin-login-implementation.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
