#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/venv/bin:${PATH}"
: "${GDAL_DATA:=}"
: "${GDAL_DRIVER_PATH:=}"
: "${GEOTIFF_CSV:=}"
: "${PROJ_LIB:=}"
: "${LIBXML2_DIR:=}"
MMSEG_SOURCE="/app/backend/model/mmseg_config/dinov3_swinV1"
if [ -d "${MMSEG_SOURCE}/mmseg" ]; then
  export PYTHONPATH="${MMSEG_SOURCE}:${PYTHONPATH:-}"
fi

/opt/venv/bin/python /app/docker/wait-for-mysql.py
cd /app/backend
/opt/venv/bin/python /app/docker/check-inference-runtime.py
exec /opt/venv/bin/python run_inference_worker.py
