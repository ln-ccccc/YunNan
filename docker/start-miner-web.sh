#!/usr/bin/env bash
set -euo pipefail

python /app/docker/write-runtime-env.py

cd /app/miner
# 依赖预检（2026-11-05 迁移审查 P1-5 修正）：本容器 node_modules 来自镜像基座，
# 主树 package.json 新增依赖而未同步镜像时，webpack 深处才炸 MODULE_NOT_FOUND、
# 排障困难——启动时显式点名缺包（miner-web 未 bind 主树 node_modules，与
# frontend 容器不同，存在真实漂移面）
env PATH=/opt/node20/bin:$PATH node /app/docker/check-node-deps.mjs /app/miner || {
  echo "[start-miner-web] 依赖预检失败：请在主树 npm install 后重启容器，或将缺包固化进镜像" >&2
  exit 1
}
env PATH=/opt/node20/bin:$PATH npm run build
exec env PATH=/opt/node20/bin:$PATH PROXY_API_TARGET="${PROXY_API_TARGET:-http://miner-api:8000}" node /app/docker/static-server.mjs /app/miner/dist "${MINER_FRONTEND_PORT:-4000}" /index.html
