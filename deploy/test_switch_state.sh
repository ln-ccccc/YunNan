#!/usr/bin/env bash
# 切换脚本里"切换状态"和"管道里的中文路径"这两段的行为验证。不起任何容器：跑的是脚本自己的函数和 release_tool.py。
#
# 针对的是复查时发现的两个缺陷（工具的单测直接调 Python 函数，没走 shell 这一段，所以没测出来）：
#   1. 状态值多包了一层引号：--rollback 判断"上一次是否成功"永远不成立，切换成功后回滚会回到当前这一份，而不是上一份；
#   2. Python 进程按系统默认的 GBK 读写管道：D:/项目/... 经过管道成了乱码路径。开发会话里恰好带着 PYTHONUTF8=1 才没发现，
#      所以本测试一开始先去掉这两个变量，按操作员普通终端的环境跑。
# 用法: bash deploy/test_switch_state.sh
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$HERE"
export SCRIPT_DIR
failures=0
check() { if [ "$1" = 0 ]; then echo "[通过] $2"; else echo "[失败] $2"; failures=$((failures + 1)); fi; }

# 原样执行脚本开头（到 native 函数为止，其中有 PYTHONUTF8=1）和要验证的那几个函数
setup() {
  local name
  unset PYTHONUTF8 PYTHONIOENCODING
  eval "$(sed -n '/^set -Eeuo pipefail/,/^}/p' "$1/switch_release.sh")"
  eval "$(sed -n -e '/^tool() /p' -e '/^json_get() /p' -e '/^quoted() /p' -e '/^state_get() /p' \
    -e '/^state_update() {/,/^}/p' -e '/^running_release() {/,/^}/p' -e '/^fail() {/,/^}/p' "$1/switch_release.sh")"
  for name in tool json_get quoted state_get state_update running_release fail; do
    [ "$(type -t "$name")" = function ] || { echo "脚本里找不到函数 $name（改名了？请同步本测试）" >&2; exit 2; }
  done
  NATIVE_TOOL="$(native "$1/release_tool.py")"
}
setup "$SCRIPT_DIR"
set +e

WORK="$(native "$(mktemp -d)")"
trap 'rm -rf -- "$WORK"' EXIT
RELEASES="$WORK/yunnan-releases"
mkdir -p "$RELEASES/基线-20261124T191117Z" "$RELEASES/候选-20261125T000000Z"
echo '{}' > "$RELEASES/基线-20261124T191117Z/RELEASE.json"
STATE_FILE="$RELEASES/.switch-state.json"

echo "1. 中文路径经过工具和管道之后还是原来的样子"
ENV_FILE="$WORK/rehearsal.env"
printf '%s\n' 'MYSQL_PASSWORD=not-a-real-one' "YUNNAN_SOURCE_ROOT=$RELEASES/候选-20261125T000000Z" > "$ENV_FILE"
got="$(tool pinned "$ENV_FILE" YUNNAN_SOURCE_ROOT | json_get YUNNAN_SOURCE_ROOT)"
[ "$got" = "$RELEASES/候选-20261125T000000Z" ]; check $? "工具写到管道里的路径被原样读回（得到：$got）"

echo "2. 切换状态：值就是值，不带引号；空值记成 null"
state_update "last_attempt=$(quoted "$RELEASES/候选-20261125T000000Z")" "last_attempt_status=\"in_progress\"" \
  "restore_to=$(quoted "$RELEASES/基线-20261124T191117Z")"
state_update "current=$(quoted "$RELEASES/候选-20261125T000000Z")" "previous=$(quoted "$RELEASES/基线-20261124T191117Z")" \
  "last_attempt_status=\"succeeded\""
[ "$(state_get last_attempt_status)" = "succeeded" ]; check $? "succeeded 存的是值，不是带引号的字符串（得到：$(state_get last_attempt_status)）"
[ "$(state_get current)" = "$RELEASES/候选-20261125T000000Z" ]; check $? "current 是切到的那一份，中文路径完好"
[ "$(state_get previous)" = "$RELEASES/基线-20261124T191117Z" ]; check $? "previous 是它之前的那一份"
[ "$(state_get restore_to)" = "$RELEASES/基线-20261124T191117Z" ]; check $? "restore_to 是出事时该回到的那一份"
state_update "current=null" "previous=$(quoted "")"
[ -z "$(state_get current)" ] && [ -z "$(state_get previous)" ]; check $? "空值记成 null，读回来是空串"

echo "3. 写过 .env 之后失败：横幅报出现在指向哪里，恢复命令里的目录是一份真实的发布目录"
# fail 只在主 shell 里打印（管道和命令替换里的失败交给外层统一报），所以放到一个独立的 bash 进程里跑
export -f setup
code=0
bash -c 'setup "$SCRIPT_DIR"
  PIN_FILE="$1"; STATE_FILE="$2"; PINNED=1; TARGET_NAME="候选-20261125T000000Z"; RESTORE_TARGET="$3"
  fail "演练：后端阶段没有在 900 秒内健康"' _ "$ENV_FILE" "$STATE_FILE" "$RELEASES/基线-20261124T191117Z" 2> "$WORK/banner.txt" || code=$?
[ "$code" = 1 ]; check $? "退出码是 1（得到 $code）"
grep -q "现在指向 候选-20261125T000000Z" "$WORK/banner.txt"; check $? "横幅写明 .env 现在指向哪一份"
recovery="$(sed -n 's/^ *恢复：deploy\/switch_release.sh //p' "$WORK/banner.txt")"
target="${recovery#--rollback \"}"; target="${target%\"}"
[ "$recovery" = "--rollback \"$RELEASES/基线-20261124T191117Z\"" ] && [ -f "$target/RELEASE.json" ]
check $? "恢复命令可以直接照着执行（得到：$recovery）"
[ "$(state_get last_attempt_status)" = "failed" ]; check $? "状态记成 failed，--rollback 据此回到切换之前的那一份（得到：$(state_get last_attempt_status)）"

echo "4. 切换之前栈在哪一份上：先看 .env 钉住的，再看状态文件记的"
[ "$(running_release "$RELEASES/候选-20261125T000000Z" "$RELEASES/基线-20261124T191117Z")" = "$RELEASES/基线-20261124T191117Z" ]
check $? ".env 钉住的目录不是发布目录（没有 RELEASE.json）时不认它"
echo '{}' > "$RELEASES/候选-20261125T000000Z/RELEASE.json"
[ "$(running_release "$RELEASES/候选-20261125T000000Z" "$RELEASES/基线-20261124T191117Z")" = "$RELEASES/候选-20261125T000000Z" ]
check $? "两份都有效时取 .env 钉住的那一份"
[ "$(running_release "" "$RELEASES/基线-20261124T191117Z")" = "$RELEASES/基线-20261124T191117Z" ]; check $? ".env 没钉住时取状态文件记的那一份"
[ -z "$(running_release "" "")" ]; check $? "都没有就是空串（开发树）"

echo
if [ "$failures" -eq 0 ]; then echo "全部通过"; else echo "有 $failures 项没有通过"; fi
[ "$failures" -eq 0 ]
