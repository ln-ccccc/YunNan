#!/usr/bin/env bash
# 定稿回归运行器：把被测源码钉死在一个 commit 上，并记录依赖版本与数据集指纹。
# 用法: bash run_final_regression.sh <commit> [workers]
set -euo pipefail
REPO="D:/项目/YunNan"
WORK="${ZC_GEOM_WORK:-D:/YunNan-Handoff/zcode-20261124-geometry}"  # 数据导出、输出、commit 导出都放在仓库外
COMMIT="$(git -C "$REPO" rev-parse "$1")"
SHORT="${COMMIT:0:12}"
WORKERS="${2:-3}"
SRC="$WORK/src-$SHORT"
OUT="$WORK/out/final-$SHORT"
rm -rf "$SRC" "$OUT"
mkdir -p "$SRC" "$OUT"
git -C "$REPO" archive "$COMMIT" backend | tar -x -C "$SRC"
echo "commit $COMMIT" > "$OUT/binding.txt"
git -C "$REPO" log -1 --format='commit-date %cI%nsubject %s' "$COMMIT" >> "$OUT/binding.txt"
sha256sum "$SRC/backend/applications/project_hub/geometry_engine.py" \
  "$SRC/backend/applications/project_hub/classification_results.py" \
  "$SRC/backend/applications/api/project.py" \
  "$WORK/production-results.ndjson" "$WORK/before-verdicts.ndjson" "$WORK/final_regression.py" >> "$OUT/binding.txt"
wc -l "$WORK/production-results.ndjson" "$WORK/before-verdicts.ndjson" >> "$OUT/binding.txt"
git -C "$REPO" status --porcelain -- backend >> "$OUT/binding.txt" || true
MSYS_NO_PATHCONV=1 docker run --rm --network none --cpus "$WORKERS" --memory 5g --workdir /app/backend \
  --entrypoint /opt/conda/envs/MMSeg310/bin/python -e PYTHONUTF8=1 \
  -v "$SRC/backend:/app/backend:ro" -v "$WORK:/work" yunnan-runtime:current \
  /work/final_regression.py /work/production-results.ndjson /work/before-verdicts.ndjson "/work/out/final-$SHORT" "$WORKERS" > "$OUT/stdout.log" 2> "$OUT/run.log"
MSYS_NO_PATHCONV=1 docker run --rm --network none --entrypoint /opt/conda/envs/MMSeg310/bin/python yunnan-runtime:current \
  -c "import sys, shapely, shapely.geos, rasterio, numpy; print('python', sys.version.split()[0], 'shapely', shapely.__version__, 'geos', shapely.geos.geos_version_string, 'rasterio', rasterio.__version__, 'numpy', numpy.__version__)" >> "$OUT/binding.txt"
docker image inspect yunnan-runtime:current --format 'image {{.Id}}' >> "$OUT/binding.txt"
echo "done: $OUT"
