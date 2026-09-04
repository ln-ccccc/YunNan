#!/usr/bin/env bash
set -euo pipefail

source /opt/conda/etc/profile.d/conda.sh
: "${GDAL_DATA:=}"
: "${GDAL_DRIVER_PATH:=}"
: "${GEOTIFF_CSV:=}"
: "${PROJ_LIB:=}"
: "${LIBXML2_DIR:=}"
conda activate MMSeg310
export GDAL_DATA="${GDAL_DATA:-/opt/conda/envs/MMSeg310/share/gdal}"
export PROJ_LIB="${PROJ_LIB:-/opt/conda/envs/MMSeg310/share/proj}"
export PROJ_DATA="${PROJ_DATA:-${PROJ_LIB}}"

MMSEG_SOURCE="/app/backend/model/mmseg_config/dinov3_swinV1"
if [ -d "${MMSEG_SOURCE}/mmseg" ]; then
  export PYTHONPATH="${MMSEG_SOURCE}:${PYTHONPATH:-}"
fi

python /app/docker/write-runtime-env.py

cd /app/miner
exec env PATH=/opt/node20/bin:$PATH PORT="${MINER_BACKEND_PORT:-8000}" GEOVIEW_BACKEND_URL="${GEOVIEW_BACKEND_URL:-http://backend:5008}" node server.js
