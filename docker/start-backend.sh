#!/usr/bin/env bash
set -euo pipefail

source /opt/conda/etc/profile.d/conda.sh
: "${GDAL_DATA:=}"
: "${GDAL_DRIVER_PATH:=}"
: "${GEOTIFF_CSV:=}"
: "${PROJ_LIB:=}"
: "${LIBXML2_DIR:=}"
conda activate MMSeg310

MMSEG_SOURCE="/app/backend/model/mmseg_config/dinov3_swinV1"
if [ -d "${MMSEG_SOURCE}/mmseg" ]; then
  export PYTHONPATH="${MMSEG_SOURCE}:${PYTHONPATH:-}"
fi

python /app/docker/wait-for-mysql.py
python /app/docker/write-runtime-env.py

cd /app/backend
python /app/backend/seed_yunnan_project.py --kml-path /app/miner/yunnan.kml --expected-count 565
if [ "${AUTO_MIGRATE_YUNNAN_SPATIAL:-1}" = "1" ]; then
  python /app/backend/migrate_yunnan_spatial.py
fi
exec gunicorn \
  --bind "0.0.0.0:${BACKEND_PORT:-5008}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --threads "${WEB_THREADS:-4}" \
  --timeout "${WEB_TIMEOUT_SECONDS:-120}" \
  --access-logfile - \
  --error-logfile - \
  app:app
