#!/usr/bin/env bash
set -euo pipefail

python /app/docker/write-runtime-env.py

cd /app/frontend
# 依赖预检（2026-11-05 迁移审查 P1-5 修正配套）：frontend 容器当前整目录 bind
# （node_modules 随主树），预检防未来挂载方式变化时构建炸在 webpack 深处
node /app/docker/check-node-deps.mjs /app/frontend
npm run build
exec node /app/docker/static-server.mjs /app/frontend/dist "${FRONTEND_PORT:-3000}" /index.html
