#!/usr/bin/env bash
set -euo pipefail

python /app/docker/write-runtime-env.py

cd /app/frontend
npm run build
exec node /app/docker/static-server.mjs /app/frontend/dist "${FRONTEND_PORT:-3000}" /index.html
