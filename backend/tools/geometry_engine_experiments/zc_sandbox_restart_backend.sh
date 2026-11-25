#!/usr/bin/env bash
# 只重启隔离沙盒里的后端容器，换成指定 commit 的只读导出（生产容器不动）。
# 用法: bash zc_sandbox_restart_backend.sh <commit>
set -euo pipefail
REPO="D:/项目/YunNan"
WORK="${ZC_GEOM_WORK:-D:/YunNan-Handoff/zcode-20261124-geometry}"
COMMIT_FULL="$(git -C "$REPO" rev-parse "$1")"
SRC="$WORK/src-${COMMIT_FULL:0:12}"
export MSYS_NO_PATHCONV=1
test -f "$SRC/backend/applications/project_hub/geometry_engine.py" || { echo "缺少 commit 导出目录: $SRC（先跑 run_final_regression.sh）"; exit 1; }
test -f "$WORK/zc-geom.env" || { echo "缺少环境文件：先跑 zc_sandbox_up.sh"; exit 1; }
docker rm -f zc-geom-backend >/dev/null 2>&1 || true
docker run -d --name zc-geom-backend --network zc-geom-net -p "127.0.0.1:5018:5008" --env-file "$WORK/zc-geom.env" \
  --entrypoint bash -w /app \
  -v "$SRC/backend:/app/backend:ro" \
  -v "$REPO/config.yaml:/app/config.yaml:ro" \
  -v "$REPO/project_storage:/project_storage" \
  -v "$REPO/maps/dali:/project_storage/incoming/dali:ro" \
  -v "$REPO/miner/yunnan.kml:/app/miner/yunnan.kml:ro" \
  -v "zc-geom-outputs:/app/miner/change_matrix_outputs" \
  -v "zc-geom-uploads:/app/miner/uploads" \
  yunnan-runtime:current -c "source /opt/conda/etc/profile.d/conda.sh && conda activate MMSeg310 && cd /app/backend && exec gunicorn --bind 0.0.0.0:5008 --workers 1 --threads 4 --timeout 120 app:app" >/dev/null
code=000
for attempt in $(seq 1 45); do
  code="$(curl -s -o /dev/null -w '%{http_code}' http://localhost:5018/api/auth/session || true)"
  if [ "$code" = "200" ] || [ "$code" = "401" ]; then break; fi
  sleep 2
done
echo "isolated backend now runs commit ${COMMIT_FULL:0:12}; http=$code"
sha256sum "$SRC/backend/applications/project_hub/geometry_engine.py" | cut -c1-80
