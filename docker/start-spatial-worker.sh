#!/usr/bin/env bash
set -euo pipefail

source /opt/conda/etc/profile.d/conda.sh
conda activate MMSeg310

python /app/docker/wait-for-mysql.py
cd /app/backend
exec python run_spatial_worker.py
