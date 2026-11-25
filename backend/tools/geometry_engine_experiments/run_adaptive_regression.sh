#!/usr/bin/env bash
# 自适应容差定稿回归的运行器：把被测源码钉死在一个 commit 上，并记录依赖版本与数据指纹。
# 用法: bash run_adaptive_regression.sh <commit> <all|样本 json 的容器内路径> [workers] [--repeat]
#
# 与 run_final_regression.sh 的区别：这一轮的回归要读真实 label 的头信息（走生产代码 simplify_scale），
# 所以把生产存储「只读」挂载为 /project_storage；除此之外不连库、不写任何东西。
set -euo pipefail
REPO="${ZC_REPO:-D:/项目/YunNan}"
PREV="${ZC_GEOM_WORK:-D:/YunNan-Handoff/zcode-20261124-geometry}"      # 上一轮的只读导出（生产成果 NDJSON）
WORK="${ZC_GEOM2_WORK:-D:/YunNan-Handoff/zcode-20261124-release}"      # 本轮：label 元数据、分层样本、输出
STORAGE="${ZC_PROJECT_STORAGE:-D:/项目/YunNan/project_storage}"
COMMIT="$(git -C "$REPO" rev-parse "$1")"
SHORT="${COMMIT:0:12}"
SELECTION="$2"
WORKERS="${3:-3}"
REPEAT="${4:-}"
SUFFIX=""; [ "$REPEAT" = "--repeat" ] && SUFFIX="-repeat"
SRC="$WORK/src-$SHORT"
NAME="final-$SHORT$SUFFIX"
OUT="$WORK/out/$NAME"
rm -rf "$SRC" "$OUT"
mkdir -p "$SRC" "$OUT"
git -C "$REPO" archive "$COMMIT" backend | tar -x -C "$SRC"
ENGINE="$SRC/backend/applications/project_hub"
SCRIPT_REL="tools/geometry_engine_experiments/adaptive_regression.py"
SCRIPT="$SRC/backend/$SCRIPT_REL"     # 宿主路径：算哈希用；容器内的路径是 /app/backend/$SCRIPT_REL
RUNNER="$REPO/backend/tools/geometry_engine_experiments/run_adaptive_regression.sh"
{
  echo "commit $COMMIT"
  git -C "$REPO" log -1 --format='commit-date %cI%nsubject %s' "$COMMIT"
  sha256sum "$ENGINE/geometry_engine.py" "$ENGINE/geometry_metric.py" \
    "$ENGINE/classification_results.py" "$SRC/backend/applications/api/project.py" "$SCRIPT" "$RUNNER" \
    "$PREV/production-results.ndjson" "$WORK/out/label-meta.ndjson"
  wc -l "$PREV/production-results.ndjson" "$WORK/out/label-meta.ndjson"
  [ "$SELECTION" = "all" ] || sha256sum "$WORK/$SELECTION"
  echo "selection $SELECTION workers $WORKERS repeat ${REPEAT:-no}"
  echo "labels: read live, read-only, from $STORAGE (listed in label-meta.ndjson)"
  echo "tree dirty at run time: $(git -C "$REPO" status --porcelain -- backend | wc -l) changed paths (not used: the run reads the git archive above)"
} > "$OUT/binding.txt"
MSYS_NO_PATHCONV=1 docker run --rm --network none --cpus "$WORKERS" --memory 2g --workdir /app/backend \
  --entrypoint /opt/conda/envs/MMSeg310/bin/python -e PYTHONUTF8=1 \
  -e PROJ_LIB=/opt/conda/envs/MMSeg310/share/proj -e GDAL_DATA=/opt/conda/envs/MMSeg310/share/gdal \
  -e PROJ_DATA=/opt/conda/envs/MMSeg310/share/proj -e PROJECT_STORAGE_ROOT=/project_storage \
  -v "$SRC/backend:/app/backend:ro" -v "$STORAGE:/project_storage:ro" -v "$WORK:/work" \
  -v "$PREV/production-results.ndjson:/prev/production-results.ndjson:ro" \
  yunnan-runtime:current "/app/backend/$SCRIPT_REL" /prev/production-results.ndjson /work/out/label-meta.ndjson \
  "/work/out/$NAME" "$SELECTION" "$WORKERS" $REPEAT > "$OUT/stdout.log" 2> "$OUT/run.log"
MSYS_NO_PATHCONV=1 docker run --rm --network none --entrypoint /opt/conda/envs/MMSeg310/bin/python yunnan-runtime:current \
  -c "import sys, shapely, shapely.geos, rasterio, rasterio.warp, numpy; print('python', sys.version.split()[0], 'shapely', shapely.__version__, 'geos', shapely.geos.geos_version_string, 'rasterio', rasterio.__version__, 'gdal', rasterio.__gdal_version__, 'proj', rasterio.__proj_version__, 'numpy', numpy.__version__)" >> "$OUT/binding.txt"
docker image inspect yunnan-runtime:current --format 'image {{.Id}}' >> "$OUT/binding.txt"
date '+finished %Y-%m-%dT%H:%M:%S%z' >> "$OUT/binding.txt"
echo "done: $OUT"
