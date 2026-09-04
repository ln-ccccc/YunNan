#!/usr/bin/env bash
set -euo pipefail

python /app/docker/write-runtime-env.py

cd /app/miner
env PATH=/opt/node20/bin:$PATH npm run build
exec env PATH=/opt/node20/bin:$PATH PROXY_API_TARGET="${PROXY_API_TARGET:-http://miner-api:8000}" node /app/docker/static-server.mjs /app/miner/dist "${MINER_FRONTEND_PORT:-4000}" /index.html
