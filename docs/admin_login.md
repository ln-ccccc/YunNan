# 管理员登录说明

## 环境变量

```text
ADMIN_USERNAME=admin
ADMIN_PASSWORD=必须显式设置强密码
```

- 管理员用户名固定为 `admin`。
- 环境变量 `ADMIN_PASSWORD` 只用于**首次建号**：空库启动时按它创建初始密码。账号一旦存在，密码权威在数据库——页面改密后重启不会被环境变量回退（2026-11-14 修复）。
- 忘记密码时不能只改 `ADMIN_PASSWORD` 重启（不会覆盖已有账号）。重置步骤：进 `yunnan-mysql` 容器执行 `DELETE FROM admin_user WHERE username='admin';`，再按上方变量重建 `backend`、`miner-api`、`miner-web` 容器，即按 `ADMIN_PASSWORD` 重新建号。
- `docker compose` 未提供 `ADMIN_PASSWORD` 时，服务不会启动。

## 认证边界

- 浏览器只保存服务端会话 cookie。
- 不把密码、JWT 或长期 token 写入 `localStorage/sessionStorage`。
- `miner` 负责把浏览器 cookie 转发给 `backend`，由 `backend` 统一鉴权。

## 认证接口

- `POST /api/auth/login`
- `GET /api/auth/session`
- `POST /api/auth/logout`

## 登录后才能访问

- `/api/projects`
- `/api/stats`
- `/api/geojson`
- `/api/mines/*`
- `/api/kml/*`
- `/api/inference/*`
- `/change-matrix-outputs/*`

## Docker 联调

```powershell
$env:APP_IMAGE="yunnan-runtime:current"
docker compose -f docker-compose.prod.yml up -d --force-recreate backend miner-api miner-web
```

## 最小验收

- 未登录打开 `http://127.0.0.1:4000/#/projects` 时只显示登录页。
- 使用 `admin + ADMIN_PASSWORD` 登录成功后进入项目工作台。
- 已登录时可以进入 `#/map`。
- 退出登录后再次访问业务 API 返回 `401`。
