#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/venv/bin:${PATH}"
: "${GDAL_DATA:=}"
: "${GDAL_DRIVER_PATH:=}"
: "${GEOTIFF_CSV:=}"
: "${PROJ_LIB:=}"
: "${LIBXML2_DIR:=}"

# compose 注入的 GDAL_DATA/PROJ_LIB 面向 conda 运行镜像（/opt/conda/envs/MMSeg310），
# 本镜像为 /opt/venv，这些路径不存在。带着无效路径运行会让 GDAL 找不到 proj.db，
# 矢量重投影直接报 "The EPSG code is unknown"（2026-09-08 端到端实测）。
# 因此校验路径存在性：无效则回退到随 rasterio 安装的资源目录。
if [ -n "${PROJ_LIB}" ] && [ ! -d "${PROJ_LIB}" ]; then
  PROJ_FALLBACK="$(dirname "$(find /opt/venv -name proj.db 2>/dev/null | head -1)")"
  if [ -n "${PROJ_FALLBACK}" ] && [ -f "${PROJ_FALLBACK}/proj.db" ]; then
    echo "[entrypoint] 注入的 PROJ_LIB 在本镜像不存在，回退到 ${PROJ_FALLBACK}" >&2
    export PROJ_LIB="${PROJ_FALLBACK}" PROJ_DATA="${PROJ_FALLBACK}"
  else
    echo "[entrypoint] 注入的 PROJ_LIB 无效且未找到回退资源，清除以使用 GDAL 内置默认" >&2
    unset PROJ_LIB PROJ_DATA
  fi
fi
if [ -n "${GDAL_DATA}" ] && [ ! -d "${GDAL_DATA}" ]; then
  GDAL_FALLBACK="$(dirname "$(find /opt/venv -name gdal_data -type d 2>/dev/null | head -1)")/gdal_data"
  if [ -d "${GDAL_FALLBACK}" ]; then
    echo "[entrypoint] 注入的 GDAL_DATA 在本镜像不存在，回退到 ${GDAL_FALLBACK}" >&2
    export GDAL_DATA="${GDAL_FALLBACK}"
  else
    unset GDAL_DATA
  fi
fi

MMSEG_SOURCE="/app/backend/model/mmseg_config/dinov3_swinV1"
if [ -d "${MMSEG_SOURCE}/mmseg" ]; then
  export PYTHONPATH="${MMSEG_SOURCE}:${PYTHONPATH:-}"
fi

/opt/venv/bin/python /app/docker/wait-for-mysql.py
cd /app/backend
/opt/venv/bin/python /app/docker/check-inference-runtime.py
exec /opt/venv/bin/python run_inference_worker.py
