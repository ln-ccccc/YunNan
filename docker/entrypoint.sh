#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${CONFIG_PATH:-/app/config.yaml}"

source /opt/conda/etc/profile.d/conda.sh

# Predefine GDAL-specific variables to avoid set -u errors during activation
: "${GDAL_DATA:=}"
: "${GDAL_DRIVER_PATH:=}"
: "${GEOTIFF_CSV:=}"
: "${PROJ_LIB:=}"
: "${LIBXML2_DIR:=}"

conda activate MMSeg310

MMSEG_SOURCE="/app/backend/model/mmseg_config/dinov3_swinV1"
if [ -d "${MMSEG_SOURCE}/mmseg" ]; then
  export PYTHONPATH="${MMSEG_SOURCE}:${PYTHONPATH:-}"
  python - <<'PY'
import importlib.util

missing = [name for name in ("prettytable", "wcwidth") if importlib.util.find_spec(name) is None]
if missing:
    print(f"[entrypoint] Missing runtime dependencies: {', '.join(missing)}. Please rebuild image to include them.", flush=True)
PY
elif python - <<'PY'
import importlib.util
import sys
sys.exit(0 if importlib.util.find_spec("mmseg") is not None else 1)
PY
then
  true
else
  echo "[entrypoint] MMSegmentation package is missing and ${MMSEG_SOURCE} was not found."
fi

CONFIG_EXPORTS=$(python - <<'PY'
import os
import yaml

config_path = os.environ.get("CONFIG_PATH", "/app/config.yaml")
with open(config_path, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

backend_host = cfg["host"]["backend"]
backend_port = cfg["port"]["backend"]
frontend_host = cfg["host"]["frontend"]
frontend_port = cfg["port"]["frontend"]

client_host = "127.0.0.1" if backend_host == "0.0.0.0" else backend_host

# Miner config
miner_cfg = cfg.get("miner", {})
miner_enabled = "true" if miner_cfg.get("enabled", False) else "false"
miner_frontend_port = miner_cfg.get("frontend_port", 4000)
miner_backend_port = miner_cfg.get("backend_port", 8000)

print(f"BACKEND_HOST={backend_host}")
print(f"BACKEND_PORT={backend_port}")
print(f"FRONTEND_HOST={frontend_host}")
print(f"FRONTEND_PORT={frontend_port}")
print(f"BACKEND_CLIENT_HOST={client_host}")
print(f"MINER_ENABLED={miner_enabled}")
print(f"MINER_FRONTEND_PORT={miner_frontend_port}")
print(f"MINER_BACKEND_PORT={miner_backend_port}")
PY
)

eval "${CONFIG_EXPORTS}"

# Write GeoView frontend .env (include Miner toggle)
cat > /app/frontend/.env <<EOF
VUE_APP_BACKEND_PORT = ${BACKEND_PORT}
VUE_APP_BACKEND_IP = ${BACKEND_CLIENT_HOST}
VUE_APP_MINER_ENABLED = ${MINER_ENABLED}
VUE_APP_MINER_URL = http://localhost:${MINER_FRONTEND_PORT}
EOF

python - <<'PY'
import os
import socket
import sys
import time

host = os.getenv("MYSQL_HOST", "127.0.0.1")
port = int(os.getenv("MYSQL_PORT", "3306"))

for attempt in range(30):
    try:
        sock = socket.create_connection((host, port), timeout=2)
        sock.close()
        break
    except Exception as exc:
        wait = 2
        print(f"[entrypoint] Waiting for MySQL at {host}:{port} (attempt {attempt + 1}/30): {exc}", flush=True)
        time.sleep(wait)
else:
    print("[entrypoint] MySQL did not become available in time, exiting.", flush=True)
    sys.exit(1)
PY

cd /app/backend
python app.py &
BACKEND_PID=$!

cd /app/frontend
npm run serve -- --host "${FRONTEND_HOST}" --port "${FRONTEND_PORT}" &
FRONTEND_PID=$!

# --- Miner (鐭垮北鐩戞祴绯荤粺) conditional startup ---
MINER_BACKEND_PID=""
MINER_FRONTEND_PID=""

if [ "${MINER_ENABLED}" = "true" ]; then
  echo "[entrypoint] Miner is ENABLED. Starting Miner services..."

  MINER_MAP_PROVIDER="$(printf '%s' "${MINER_MAP_PROVIDER:-tianditu}" | tr '[:upper:]' '[:lower:]')"
  MINER_TDT_KEY="${MINER_TDT_KEY:-}"
  MINER_LOCAL_TILE_URL="${MINER_LOCAL_TILE_URL:-}"
  MINER_LOCAL_TMS="${MINER_LOCAL_TMS:-0}"

  # Write Miner .env for GeoView URL
  cat > /app/miner/.env <<MENV
VITE_GEOVIEW_URL="http://localhost:${FRONTEND_PORT}/#/segmentation"
VITE_MINER_MAP_PROVIDER=${MINER_MAP_PROVIDER}
VITE_TDT_KEY=${MINER_TDT_KEY}
MENV

  if [ "${MINER_MAP_PROVIDER}" = "offline" ] || [ "${MINER_MAP_PROVIDER}" = "local" ]; then
    cat >> /app/miner/.env <<MENV
VITE_MINER_LOCAL_TILE_URL=${MINER_LOCAL_TILE_URL}
VITE_MINER_LOCAL_TMS=${MINER_LOCAL_TMS}
MENV
  fi

  # Start Miner Express backend (using Node.js 20)
  cd /app/miner
  PATH=/opt/node20/bin:$PATH PORT=${MINER_BACKEND_PORT} node server.js &
  MINER_BACKEND_PID=$!

  # Start Miner Vite dev server (using Node.js 20)
  cd /app/miner
  PATH=/opt/node20/bin:$PATH npx vite --host 0.0.0.0 --port "${MINER_FRONTEND_PORT}" &
  MINER_FRONTEND_PID=$!

  echo "[entrypoint] Miner backend PID=${MINER_BACKEND_PID}, frontend PID=${MINER_FRONTEND_PID}"
else
  echo "[entrypoint] Miner is DISABLED. Skipping Miner services."
fi

cd /app

terminate() {
  trap - SIGTERM SIGINT
  kill -TERM "${BACKEND_PID}" "${FRONTEND_PID}" 2>/dev/null || true
  if [ -n "${MINER_BACKEND_PID}" ]; then
    kill -TERM "${MINER_BACKEND_PID}" 2>/dev/null || true
  fi
  if [ -n "${MINER_FRONTEND_PID}" ]; then
    kill -TERM "${MINER_FRONTEND_PID}" 2>/dev/null || true
  fi
  wait "${BACKEND_PID}" "${FRONTEND_PID}" 2>/dev/null || true
  if [ -n "${MINER_BACKEND_PID}" ]; then
    wait "${MINER_BACKEND_PID}" 2>/dev/null || true
  fi
  if [ -n "${MINER_FRONTEND_PID}" ]; then
    wait "${MINER_FRONTEND_PID}" 2>/dev/null || true
  fi
}

trap terminate SIGTERM SIGINT

wait -n "${BACKEND_PID}" "${FRONTEND_PID}"
terminate
