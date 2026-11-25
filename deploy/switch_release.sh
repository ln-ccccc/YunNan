#!/usr/bin/env bash
# 把运行中的栈切到一份发布目录，切完自动核对。回滚：deploy/switch_release.sh --rollback [发布目录]。
#
# 用法：deploy/switch_release.sh <发布目录> [选项]
#       deploy/switch_release.sh --rollback [发布目录] [同样的选项]
#   --project <名>         compose 项目名，容器名是 <名>-<服务>（默认 yunnan，即生产）
#   --project-dir <目录>   compose 文件与环境文件所在的目录（默认本仓库）
#   --file <文件>          compose 文件，相对 --project-dir，可重复（默认 docker-compose.prod.yml 加 docker-compose.gpu.yml）
#   --env-file <文件>      环境文件，可重复；版本钉在第一个里（默认 .env 加 image_bundle.env）
#   --skip <服务,...>      不切也不核对的 worker（目前只有 inference-worker 用得上：演练栈没有它）
#   --config-override <文件>  演练栈挂的另一份 config.yaml（只改了端口）；生产不传
#   --with-mysql           mysql 容器不存在时先创建；已存在则完全不碰
#   --allow-running-jobs   有排队或运行中的推理 / 切片任务也照常切（默认拦下：重启 worker 会中断任务）
#   --timeout <秒>         每个阶段等健康的上限（默认 900）
#   --rollback-target <目录>  此前没有切换过（.env 还没有钉住任何发布目录）时，出问题或回滚应回到的那一份。
#                           生产第一次切换用它指向上线前的那一份；不给的话出问题只能解除钉住、回到开发树
#   --backend-url <地址>   核对用的后端地址，另有 --frontend-url、--miner-api-url、--miner-web-url
#
# 顺序：核对发布目录 → 用目标目录渲染配置并对账 → 查进行中的任务 → 写 .env →
#       后端与 worker → GeoView → Miner 适配层与界面 → 核对运行中的栈。
#
# 失败安全：
# - 前三步只读。写 .env 之前无论哪一步失败，.env 一个字节都不动。
# - 写 .env 之后再渲染一次，必须与对账过的那份逐字节相同，否则立刻把 .env 改回原样。
# - 写 .env 之后任何失败（包括 Ctrl-C）都报出 .env 现在指向哪里，并给出恢复命令
#   deploy/switch_release.sh --rollback。不带目录时按状态文件决定回到哪里
#   （状态文件与发布目录放在一起，在仓库之外）：
#     上一次切换成功       → 回到它之前的那一份；
#     切换失败、没切完     → 回到切换之前的那一份（此前没有钉住过，就是 --rollback-target；再没有就解除钉住）；
#     在写 .env 之前就停了 → 什么都没变，不需要回滚。
#   脚本不自动回滚：失败原因可能是磁盘、内存这类外部因素，再切一次未必更好，由人判断。
# - 所有等待都有上限：查任务 30 秒，每次 docker 调用 5 分钟，每个阶段的健康等待 --timeout。
# 会重启全部挂载源码的容器（5 个应用容器加 2 个 worker），请选没有任务在跑的时间窗口。
set -Eeuo pipefail
export MSYS_NO_PATHCONV=1
# 脚本里的 Python 进程经管道互传发布目录的路径，路径里有中文（D:/项目/...）。
# 不定下来的话按系统默认的 GBK 读写：工具写出的是 UTF-8，读进来成了乱码路径，对账不通过。
export PYTHONUTF8=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$HERE/release_tool.py"
native() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi
}
NATIVE_TOOL="$(native "$TOOL")"
SCRATCH="$(native "$(mktemp -d)")"
trap 'rm -rf -- "$SCRATCH"' EXIT

usage() {
  sed -n '2,/^set -Eeuo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
}

# 发布目录一律换成绝对路径：后面要 cd 到项目目录，相对路径会失效
absolute() {
  local parent
  parent="$(cd "$(dirname "$1")" 2>/dev/null && pwd)" || { echo "目录不存在：$1" >&2; exit 2; }
  native "$parent/$(basename "$1")"
}

ROLLBACK=0
RELEASE=""
ROLLBACK_TARGET=""
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
    --rollback) ROLLBACK=1; shift ;;
    --rollback-target) ROLLBACK_TARGET="$(absolute "$2")"; shift 2 ;;
    --project) PROJECT="$2"; shift 2 ;;
    --project-dir) PROJECT_DIR="$2"; shift 2 ;;
    --file) FILES+=("$2"); shift 2 ;;
    --env-file) ENV_FILES+=("$2"); shift 2 ;;
    --skip) SKIP="$2"; shift 2 ;;
    --config-override) CONFIG_ARGS=(--config-override "$(native "$2")"); shift 2 ;;
    --with-mysql) WITH_MYSQL=1; shift ;;
    --allow-running-jobs) ALLOW_JOBS=1; shift ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --backend-url|--frontend-url|--miner-api-url|--miner-web-url) VERIFY_ARGS+=("$1" "$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "未知选项：$1" >&2; usage >&2; exit 2 ;;
    *) [ -z "$RELEASE" ] || { echo "只能给一个发布目录" >&2; exit 2; }; RELEASE="$(absolute "$1")"; shift ;;
  esac
done
if [ ${#FILES[@]} -eq 0 ]; then FILES=(docker-compose.prod.yml docker-compose.gpu.yml); fi
if [ ${#ENV_FILES[@]} -eq 0 ]; then ENV_FILES=(.env image_bundle.env); fi
case "$TIMEOUT" in ''|*[!0-9]*) echo "--timeout 要给秒数：$TIMEOUT" >&2; exit 2 ;; esac
if [ -n "$ROLLBACK_TARGET" ] && [ ! -f "$ROLLBACK_TARGET/RELEASE.json" ]; then
  echo "--rollback-target 不是发布目录（没有 RELEASE.json）：$ROLLBACK_TARGET" >&2; exit 2
fi

cd "$PROJECT_DIR"
PIN_FILE="${ENV_FILES[0]}"
[ -f "$PIN_FILE" ] || { echo "环境文件不存在：$PROJECT_DIR/$PIN_FILE" >&2; exit 2; }
COMPOSE=(docker compose -p "$PROJECT")
for item in "${ENV_FILES[@]}"; do COMPOSE+=(--env-file "$item"); done
for item in "${FILES[@]}"; do COMPOSE+=(--file "$item"); done
# 外层环境里残留的值会盖过环境文件：先清掉，渲染结果只取决于环境文件和脚本显式传入的值
unset YUNNAN_SOURCE_ROOT YUNNAN_GIT_SHA
EXEC_TIMEOUT=300      # 一次 docker 调用（含 compose up）的上限（秒）
STARTED="$SECONDS"

tool() { python "$NATIVE_TOOL" "$@"; }
# 取 JSON 输出的某个键；没有就是空串
json_get() { python -c 'import json, sys; value = json.load(sys.stdin).get(sys.argv[1]); print("" if value is None else value)' "$1"; }
# 字符串转成 JSON 值；空串转成 null（不是空字符串的 JSON）
quoted() {
  if [ -n "$1" ]; then python -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$1"; else printf 'null\n'; fi
}

STATE_FILE=""         # 与发布目录放在一起：/<发布根>/.switch-state.json（见 state_file）
PINNED=0              # 写过 .env 之后置 1：此后任何失败都要报出恢复命令
TARGET_NAME=""        # 写过 .env 之后它指向哪里
RESTORE_TARGET=""     # 出问题时应回到的发布目录；空串表示解除钉住、回到开发树

state_get() { tool state show "$STATE_FILE" | json_get "$1"; }
# 把 键=JSON值 更新进切换状态；值为空串按 null 记
state_update() {
  local payload
  payload="$(python -c 'import json, sys; print(json.dumps({key: json.loads(value) if value else None for key, value in (item.split("=", 1) for item in sys.argv[1:])}))' "$@")"
  tool state update "$STATE_FILE" "$payload" >/dev/null
}

# 写 .env 之后的任何失败都从这里退出：报出 .env 现在指向哪里、怎么恢复
fail() {
  # 管道和命令替换里的失败由外层统一报，这里只把非零退出带出去，免得同一次失败打印两遍
  if [ "$BASH_SUBSHELL" -gt 0 ]; then exit 1; fi
  trap - ERR INT TERM
  echo "切换没有完成：$1" >&2
  if [ "$PINNED" = 1 ]; then
    state_update "last_attempt_status=\"failed\"" || true
    echo "  $PIN_FILE 现在指向 $TARGET_NAME。容器可能一部分已经切到它、一部分还在原来的版本上。" >&2
    if [ -n "$RESTORE_TARGET" ]; then
      echo "  恢复：deploy/switch_release.sh --rollback \"$RESTORE_TARGET\"" >&2
    else
      echo "  恢复：deploy/switch_release.sh --rollback    （解除钉住，回到开发树）" >&2
    fi
  fi
  exit 1
}
trap 'fail "第 $LINENO 行的命令没有成功：$BASH_COMMAND"' ERR
trap 'fail "被中断（第 $LINENO 行）"' INT TERM

wait_healthy() {
  local deadline=$((SECONDS + TIMEOUT))
  local pending=() status service item
  while :; do
    pending=()
    for service in "$@"; do
      status="$(timeout "${EXEC_TIMEOUT:-300}" docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$PROJECT-$service" 2>/dev/null || echo "missing none")"
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
  if ! timeout "${EXEC_TIMEOUT:-300}" "${COMPOSE[@]}" up -d --no-deps "$@"; then
    "${COMPOSE[@]}" logs --no-color --tail 60 "$@" || true
    return 1
  fi
  if ! wait_healthy "$@"; then
    "${COMPOSE[@]}" logs --no-color --tail 80 "$@" || true
    return 1
  fi
}

check_jobs() {
  if [ "$ALLOW_JOBS" = 1 ]; then return 0; fi
  if ! timeout "${EXEC_TIMEOUT:-300}" docker inspect "$PROJECT-backend" >/dev/null 2>&1; then return 0; fi   # 后端还不存在（首次上线）
  tool jobs --project "$PROJECT" || return 1
  echo "    没有排队或运行中的推理 / 切片任务"
}

has_service() {
  case ",$SKIP," in *",$1,"*) return 1 ;; esac
  return 0
}
WORKERS=()
for service in spatial-worker inference-worker; do
  if has_service "$service"; then WORKERS+=("$service"); fi
done

run_stages() {
  if [ "$WITH_MYSQL" = 1 ] && ! docker inspect "$PROJECT-mysql" >/dev/null 2>&1; then
    switch_stage "创建数据库" mysql || fail "数据库没有起来"
  fi
  switch_stage "后端与 worker" backend ${WORKERS[@]+"${WORKERS[@]}"} || fail "后端阶段没有健康（容器退出了，或等了 $TIMEOUT 秒；见上面的容器状态与日志）"
  switch_stage "GeoView" frontend || fail "GeoView 阶段没有健康（容器退出了，或等了 $TIMEOUT 秒；冷构建要四五分钟，可以加大 --timeout）"
  switch_stage "Miner 适配层与界面" miner-api miner-web || fail "Miner 阶段没有健康（容器退出了，或等了 $TIMEOUT 秒；见上面的容器状态与日志）"
}

# 切换状态文件与发布目录放在一起（仓库之外）。生产（项目名 yunnan）用 .switch-state.json；
# 别的栈（例如演练）带自己的项目名，互不干扰——否则演练留下的记录会让生产的 --rollback 回错版本
state_file() {
  if [ "$PROJECT" = yunnan ]; then echo "$1/.switch-state.json"; else echo "$1/.switch-state.$PROJECT.json"; fi
}

# 这次切换之前栈在哪一份发布目录上：先看 .env 钉住的，再看状态文件记的；都不是发布目录就输出空（开发树）
running_release() {
  local candidate
  for candidate in "$@"; do
    if [ -n "$candidate" ] && [ -f "$candidate/RELEASE.json" ]; then printf '%s\n' "$candidate"; return 0; fi
  done
  return 0
}

# 把 .env 里钉住的版本改回钉之前的样子：原来钉在哪就钉回哪，原来没钉就去掉那两行
restore_pin() {
  if [ -n "$pinned_before" ]; then
    tool pin "$PIN_FILE" "YUNNAN_SOURCE_ROOT=$pinned_before" "YUNNAN_GIT_SHA=${old_sha:-unknown}"
  else
    tool unpin "$PIN_FILE"
  fi
}

switch_to() {
  local release="$1" short left="" pinned_before old_sha
  [ -f "$release/RELEASE.json" ] || { echo "不是发布目录（没有 RELEASE.json）：$release" >&2; exit 2; }
  STATE_FILE="$(state_file "$(dirname "$release")")"
  TARGET_NAME="$(python -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["name"])' "$release/RELEASE.json")"
  short="$(python -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["commit_short"])' "$release/RELEASE.json")"

  echo "[1/6] 核对发布目录"
  tool check "$release" --env-file "$PIN_FILE" || exit 1

  echo "[2/6] 用目标目录渲染配置并对账（此时 .env 没有动）"
  if ! YUNNAN_SOURCE_ROOT="$release" YUNNAN_GIT_SHA="$short" "${COMPOSE[@]}" config --format json > "$SCRATCH/planned.json" 2> "$SCRATCH/render.err"; then
    cat "$SCRATCH/render.err" >&2
    echo "compose 渲染失败，什么都没有动。" >&2; exit 1
  fi
  tool render-check "$release" --env-file "$PIN_FILE" ${CONFIG_ARGS[@]+"${CONFIG_ARGS[@]}"} < "$SCRATCH/planned.json" || {
    echo "渲染结果对账没通过，什么都没有动。" >&2; exit 1; }

  echo "[3/6] 查进行中的任务"
  check_jobs || { echo "有任务在跑或查不到任务数，什么都没有动。" >&2; exit 1; }

  echo "[4/6] 把版本钉进 $PIN_FILE"
  pinned_before="$(tool pinned "$PIN_FILE" YUNNAN_SOURCE_ROOT | json_get YUNNAN_SOURCE_ROOT)"
  old_sha="$(tool pinned "$PIN_FILE" YUNNAN_GIT_SHA | json_get YUNNAN_GIT_SHA)"
  if [ -n "$pinned_before" ] && [ ! -f "$pinned_before/RELEASE.json" ]; then
    echo "注意：$PIN_FILE 原先钉住的不是发布目录（$pinned_before），出问题时只能解除钉住、回到开发树。" >&2
  fi
  left="$(running_release "$pinned_before" "$(state_get current)")"
  if [ -z "$left" ] && [ "$ROLLBACK" = 0 ]; then left="$ROLLBACK_TARGET"; fi
  RESTORE_TARGET="$left"
  # 先记下这次要切到哪、出事回到哪，再动 .env：两步之间被打断，--rollback 也知道该怎么办
  state_update "last_attempt=$(quoted "$release")" "last_attempt_status=\"in_progress\"" "restore_to=$(quoted "$left")"
  if ! tool pin "$PIN_FILE" "YUNNAN_SOURCE_ROOT=$release" "YUNNAN_GIT_SHA=$short"; then
    state_update "last_attempt_status=\"aborted\""
    echo "写 .env 失败，什么都没有动。" >&2; exit 1
  fi
  PINNED=1
  if ! "${COMPOSE[@]}" config --format json > "$SCRATCH/after-pin.json" 2> "$SCRATCH/render.err"; then
    cat "$SCRATCH/render.err" >&2; fail "写 .env 之后渲染失败"
  fi
  if ! cmp -s "$SCRATCH/planned.json" "$SCRATCH/after-pin.json"; then
    # 钉住之后渲染出的配置与对账过的那份不同：把 .env 改回原样，不切换
    restore_pin
    if [ "$(tool pinned "$PIN_FILE" YUNNAN_SOURCE_ROOT | json_get YUNNAN_SOURCE_ROOT)" != "$pinned_before" ]; then
      fail "写 .env 之后渲染出的配置与对账过的那份不一致，改回 .env 之后核对不上"
    fi
    PINNED=0
    state_update "last_attempt_status=\"aborted\""
    echo "写 .env 之后渲染出的配置与对账过的那份不一致，已把 .env 改回原样，什么都没有切换。" >&2
    exit 1
  fi

  echo "[5/6] 分阶段切换"
  run_stages

  echo "[6/6] 核对运行中的栈"
  tool verify "$release" --project "$PROJECT" --skip "$SKIP" --env-file "$PIN_FILE" \
    ${CONFIG_ARGS[@]+"${CONFIG_ARGS[@]}"} ${VERIFY_ARGS[@]+"${VERIFY_ARGS[@]}"} || fail "切换已完成，但核对有不符项，见上"
  PINNED=0
  state_update "current=$(quoted "$release")" "previous=$(quoted "$left")" "last_attempt_status=\"succeeded\""
  echo "切换完成，用时 $((SECONDS - STARTED)) 秒。运行的是 $TARGET_NAME（$short）。"
  if [ -n "$left" ]; then
    echo "回滚：deploy/switch_release.sh --rollback    （回到 $(basename "$left")）"
  else
    echo "回滚：deploy/switch_release.sh --rollback    （此前没有钉住过，会解除钉住、回到开发树）"
  fi
}

# 解除钉住、回到开发树。参数：这次要离开的发布目录（状态文件从它那里推断），没有就给空串
unpin_and_up() {
  local leaving="${1:-}"
  if [ -n "$leaving" ]; then STATE_FILE="$(state_file "$(dirname "$leaving")")"; fi
  echo "[1/4] 查进行中的任务"
  check_jobs || { echo "有任务在跑或查不到任务数，什么都没有动。" >&2; exit 1; }
  echo "[2/4] 解除钉住（$PIN_FILE 只去掉那两行）"
  TARGET_NAME="开发树（解除钉住）"
  RESTORE_TARGET="$leaving"
  state_update "last_attempt=\"unpin\"" "last_attempt_status=\"in_progress\"" "restore_to=$(quoted "$leaving")"
  if ! tool unpin "$PIN_FILE"; then
    state_update "last_attempt_status=\"aborted\""
    echo "解除钉住失败，什么都没有动。" >&2; exit 1
  fi
  PINNED=1
  echo "[3/4] 分阶段切回"
  run_stages
  PINNED=0
  state_update "current=null" "previous=$(quoted "$leaving")" "last_attempt_status=\"succeeded\""
  echo "[4/4] 已回到开发树，用时 $((SECONDS - STARTED)) 秒。没有发布目录可以核对；代码版本就是 $PROJECT_DIR 当前检出的内容。"
}

if [ "$ROLLBACK" = 0 ]; then
  [ -n "$RELEASE" ] || { usage >&2; exit 2; }
  switch_to "$RELEASE"
  exit 0
fi

# ---- --rollback ----
if [ -n "$RELEASE" ]; then
  switch_to "$RELEASE"
  exit 0
fi
# 不给目录：按状态文件回到该回的那一份
pinned_root="$(tool pinned "$PIN_FILE" YUNNAN_SOURCE_ROOT | json_get YUNNAN_SOURCE_ROOT)"
[ -n "$pinned_root" ] || {
  echo "当前没有钉住任何发布目录，没有可回滚的切换。要切到某一份请直接给出：deploy/switch_release.sh --rollback <发布目录>" >&2; exit 2; }
STATE_FILE="$(state_file "$(dirname "$(native "$pinned_root")")")"
target=""
case "$(state_get last_attempt_status)" in
  succeeded)
    target="$(state_get previous)"      # 上一次切换成功 → 回到它之前的那一份
    if [ -z "$target" ]; then
      unpin_and_up "$pinned_root"       # 它之前没有别的发布目录（首次上线）→ 解除钉住、回到开发树
    else
      echo "回滚到：$target"
      switch_to "$target"
    fi ;;
  failed|in_progress)
    target="$(state_get restore_to)"    # 失败或没切完 → 回到切换之前的那一份
    if [ -z "$target" ]; then
      echo "上一次切换没有完成，此前没有钉住过，解除钉住、回到开发树。"
      unpin_and_up ""
    else
      echo "上一次切换没有完成，回到切换之前的：$target"
      switch_to "$target"
    fi ;;
  aborted)
    echo "上一次切换在改动 .env 之前就停了，运行的版本没有变，不需要回滚。"
    exit 0 ;;
  *)
    echo "状态文件里没有可用的记录（$STATE_FILE），无从得知回到哪一份；请直接给出要回到的发布目录。" >&2
    exit 2 ;;
esac
