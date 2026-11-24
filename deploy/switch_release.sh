#!/usr/bin/env bash
# 把运行中的栈切到一份发布目录，切完自动核对。回滚就是把 <发布目录> 换成上一份发布目录，再跑一次。
#
# 用法：deploy/switch_release.sh <发布目录> [选项]
#   --project <名>         compose 项目名，容器名是 <名>-<服务>（默认 yunnan，即生产）
#   --project-dir <目录>   compose 文件与环境文件所在的目录（默认本仓库）
#   --file <文件>          compose 文件，相对 --project-dir，可重复（默认 docker-compose.prod.yml 加 docker-compose.gpu.yml）
#   --env-file <文件>      环境文件，可重复；版本钉在第一个里（默认 .env 加 image_bundle.env）
#   --skip <服务,...>      这次不切也不核对的服务，例如演练栈没有的 inference-worker
#   --config-override <文件>  演练栈挂的另一份 config.yaml（只改了端口）；生产不传
#   --with-mysql           mysql 容器不存在时先创建；已存在则完全不碰
#   --allow-running-jobs   有排队或运行中的推理 / 切片任务也照常切（默认拦下：重启 worker 会中断任务）
#   --timeout <秒>         每个阶段等健康的上限（默认 900）
#   --backend-url <地址>   核对用的后端地址，另有 --frontend-url、--miner-api-url、--miner-web-url
#
# 顺序：后端与两个 worker → GeoView → Miner 适配层与界面。每一阶段等到健康才进下一阶段。
# 任一阶段失败即停：打印日志尾部和回滚命令，不自动回滚。
# 会重启全部挂载源码的容器（5 个应用容器加 2 个 worker），请选没有任务在跑的时间窗口。
set -euo pipefail
export MSYS_NO_PATHCONV=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$HERE/release_tool.py"
native() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi
}
NATIVE_TOOL="$(native "$TOOL")"

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
}

RELEASE=""
PROJECT="yunnan"
PROJECT_DIR="$(cd "$HERE/.." && pwd)"
FILES=()
ENV_FILES=()
SKIP=""
WITH_MYSQL=0
ALLOW_JOBS=0
TIMEOUT=900
VERIFY_ARGS=()
CONFIG_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --project-dir) PROJECT_DIR="$2"; shift 2 ;;
    --file) FILES+=("$2"); shift 2 ;;
    --env-file) ENV_FILES+=("$2"); shift 2 ;;
    --skip) SKIP="$2"; shift 2 ;;
    --config-override) CONFIG_ARGS=(--config-override "$2"); shift 2 ;;
    --with-mysql) WITH_MYSQL=1; shift ;;
    --allow-running-jobs) ALLOW_JOBS=1; shift ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --backend-url|--frontend-url|--miner-api-url|--miner-web-url) VERIFY_ARGS+=("$1" "$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "未知选项：$1" >&2; usage >&2; exit 2 ;;
    *) [ -z "$RELEASE" ] || { echo "只能给一个发布目录" >&2; exit 2; }; RELEASE="$1"; shift ;;
  esac
done
[ -n "$RELEASE" ] || { usage >&2; exit 2; }
if [ ${#FILES[@]} -eq 0 ]; then FILES=(docker-compose.prod.yml docker-compose.gpu.yml); fi
if [ ${#ENV_FILES[@]} -eq 0 ]; then ENV_FILES=(.env image_bundle.env); fi

cd "$PROJECT_DIR"
RELEASE="$(native "$RELEASE")"
[ -f "$RELEASE/RELEASE.json" ] || { echo "不是发布目录（没有 RELEASE.json）：$RELEASE" >&2; exit 2; }
PIN_FILE="${ENV_FILES[0]}"
[ -f "$PIN_FILE" ] || { echo "环境文件不存在：$PROJECT_DIR/$PIN_FILE" >&2; exit 2; }
COMPOSE=(docker compose -p "$PROJECT")
for item in "${ENV_FILES[@]}"; do COMPOSE+=(--env-file "$item"); done
for item in "${FILES[@]}"; do COMPOSE+=(--file "$item"); done
STARTED="$SECONDS"

echo "[1/5] 核对发布目录"
python "$NATIVE_TOOL" check "$RELEASE" --env-file "$PIN_FILE"
read -r NAME SHORT < <(python -c 'import json, sys; m = json.load(open(sys.argv[1], encoding="utf-8")); print(m["name"], m["commit_short"])' "$RELEASE/RELEASE.json" | tr -d '\r')

echo "[2/5] 把版本钉进 $PIN_FILE"
# 钉在环境文件里而不是只靠本次命令的环境变量：之后任何人按旧习惯执行 docker compose ... up -d，
# 读到的也是这同一个源码目录，不会把容器悄悄重建回开发树
CHANGES="$(python "$NATIVE_TOOL" pin "$PIN_FILE" "YUNNAN_SOURCE_ROOT=$RELEASE" "YUNNAN_GIT_SHA=$SHORT" | tr -d '\r')"
echo "    $CHANGES"
unset YUNNAN_SOURCE_ROOT YUNNAN_GIT_SHA   # 外层环境里残留的旧值会盖过环境文件

echo "[3/5] 渲染配置并对账"
RENDERED="$(mktemp)"
trap 'rm -f -- "$RENDERED"' EXIT
if ! "${COMPOSE[@]}" config --format json > "$RENDERED"; then
  echo "compose 渲染失败，什么都没有动。" >&2
  exit 1
fi
python "$NATIVE_TOOL" render-check "$RELEASE" --env-file "$PIN_FILE" ${CONFIG_ARGS[@]+"${CONFIG_ARGS[@]}"} < "$RENDERED"

has_service() {
  case ",$SKIP," in *",$1,"*) return 1 ;; esac
  return 0
}
WORKERS=()
for service in spatial-worker inference-worker; do
  if has_service "$service"; then WORKERS+=("$service"); fi
done

if [ "$ALLOW_JOBS" = 0 ] && docker inspect "$PROJECT-backend" >/dev/null 2>&1; then
  JOB_COUNTS_SQL="SELECT (SELECT COUNT(*) FROM inference_jobs WHERE status IN ('queued', 'running')), (SELECT COUNT(*) FROM project_spatial_job WHERE status IN ('pending', 'queued', 'running'));"
  # 口令不出容器：客户端从数据库容器自己的环境变量里取
  RUNNING="$(printf '%s\n' "$JOB_COUNTS_SQL" \
    | docker exec -i "$PROJECT-mysql" sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" exec mysql -u"$MYSQL_USER" -N -B "$MYSQL_DATABASE"' 2>&1 || true)"
  read -r INFERENCE_JOBS SPATIAL_JOBS <<< "$(echo "$RUNNING" | tr -s ' \t' ' ')"
  if ! [[ "$INFERENCE_JOBS" =~ ^[0-9]+$ && "$SPATIAL_JOBS" =~ ^[0-9]+$ ]]; then
    echo "查不到进行中的任务数，不敢切。确认没有任务在跑可用 --allow-running-jobs。查询输出：$RUNNING" >&2
    exit 1
  fi
  if [ "$INFERENCE_JOBS" != 0 ] || [ "$SPATIAL_JOBS" != 0 ]; then
    echo "有任务在跑（推理 $INFERENCE_JOBS 个，切片 $SPATIAL_JOBS 个），现在切换会中断它们。" >&2
    echo "等它们结束再切；确认可以中断（例如残留的僵尸任务）用 --allow-running-jobs。" >&2
    exit 1
  fi
  echo "    没有排队或运行中的推理 / 切片任务"
fi

# 等一组服务健康。容器只要还在运行就继续等：GeoView 冷构建要四五分钟，期间健康探测会先连败成"不健康"、
# 构建完再转"健康"，那不是故障。退出、重启循环、不见了才算失败。
# TIMEOUT 是全局选项，这里没有位置参数要跳过。（2026-11-25 演练抓到：这里曾多写一句 shift，把每阶段的
# 第一个服务名丢掉，于是从没等过后端，单服务的阶段更是直接跳过等待。）
wait_healthy() {
  local deadline=$((SECONDS + TIMEOUT))
  local pending=() status service item
  while :; do
    pending=()
    for service in "$@"; do
      status="$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$PROJECT-$service" 2>/dev/null || echo "missing none")"
      [ "$status" = "running healthy" ] || pending+=("$service=$status")
    done
    [ ${#pending[@]} -eq 0 ] && return 0
    for item in "${pending[@]}"; do
      case "$item" in
        *=running\ *) ;;   # 还在运行（启动中 / 暂时不健康 / 没有健康检查），继续等
        *) echo "容器状态不对：${pending[*]}" >&2; return 1 ;;
      esac
    done
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "等了 $TIMEOUT 秒还没健康：${pending[*]}" >&2
      return 1
    fi
    sleep 5
  done
}

switch_stage() {
  local label="$1"; shift
  echo "==> $label：$*"
  if ! "${COMPOSE[@]}" up -d --no-deps "$@"; then
    "${COMPOSE[@]}" logs --no-color --tail 60 "$@" || true
    return 1
  fi
  if ! wait_healthy "$@"; then
    "${COMPOSE[@]}" logs --no-color --tail 80 "$@" || true
    return 1
  fi
}

echo "[4/5] 分阶段切换"
if [ "$WITH_MYSQL" = 1 ] && ! docker inspect "$PROJECT-mysql" >/dev/null 2>&1; then
  switch_stage "创建数据库" mysql
fi
switch_stage "后端与 worker" backend "${WORKERS[@]}"
switch_stage "GeoView" frontend
switch_stage "Miner 适配层与界面" miner-api miner-web

echo "[5/5] 核对运行中的栈"
python "$NATIVE_TOOL" verify "$RELEASE" --project "$PROJECT" --skip "$SKIP" --env-file "$PIN_FILE" \
  ${CONFIG_ARGS[@]+"${CONFIG_ARGS[@]}"} ${VERIFY_ARGS[@]+"${VERIFY_ARGS[@]}"} || {
  echo "切换已完成但核对有不符项，见上。回滚：deploy/switch_release.sh <上一份发布目录>" >&2
  exit 1
}
echo "切换完成，用时 $((SECONDS - STARTED)) 秒。运行的是 $NAME（$SHORT）。"
