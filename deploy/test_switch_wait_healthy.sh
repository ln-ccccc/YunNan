#!/usr/bin/env bash
# 切换脚本里"等一组服务健康"这个函数的行为验证。用真实的小容器（不挂任何卷，几十秒后自动退出）走四条路径：
#   1. 12 秒后才就绪            → 必须等到就绪才返回，用时不少于 12 秒
#   2. 先"不健康"再恢复         → 不许把中途的"不健康"当成失败
#   3. 第一个服务名没有被丢掉    → 一次给两个服务，两个都要等到（曾经多写一句 shift 把第一个丢了）
#   4. 容器退出了               → 立即报失败，不等到超时
# 用法: bash deploy/test_switch_wait_healthy.sh
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
eval "$(sed -n '/^wait_healthy() {/,/^}/p' "$HERE/switch_release.sh")"
if command -v cygpath >/dev/null 2>&1; then export MSYS_NO_PATHCONV=1; fi
PREFIX="yunnan-wait-probe"
failures=0

check() { if [ "$1" = 0 ]; then echo "[通过] $2"; else echo "[失败] $2"; failures=$((failures + 1)); fi; }

cleanup() { docker rm -f "${PREFIX}-a" "${PREFIX}-b" "${PREFIX}-dead" >/dev/null 2>&1; }
trap cleanup EXIT
cleanup

start() { docker run -d --name "$1" --health-cmd 'test -f /tmp/ready' --health-interval 1s --health-retries 2 --health-start-period 0s \
  --entrypoint sleep yunnan-runtime:current 300 >/dev/null || { echo "起不了探测容器"; exit 2; }; }
ready_later() { ( sleep "$2"; docker exec "$1" touch /tmp/ready ) >/dev/null 2>&1 & }

echo "1. 12 秒后就绪"
start "${PREFIX}-a"; ready_later "${PREFIX}-a" 12
SECONDS=0; PROJECT="yunnan-wait" TIMEOUT=60 wait_healthy probe-a; code=$?
[ $code -eq 0 ] && [ "$SECONDS" -ge 10 ]; check $? "等到就绪才返回（返回码 $code，用时 ${SECONDS}s）"

echo "2. 中途不健康（探测 1 秒一次、2 次就判不健康），14 秒后恢复"
start "${PREFIX}-b"; ready_later "${PREFIX}-b" 14
sleep 5
docker inspect --format '   第 5 秒的健康状态：{{.State.Health.Status}}' "${PREFIX}-b"
SECONDS=0; PROJECT="yunnan-wait" TIMEOUT=60 wait_healthy probe-b; code=$?
[ $code -eq 0 ] && [ "$SECONDS" -ge 8 ]; check $? "不健康之后恢复，按成功处理（返回码 $code，用时 ${SECONDS}s）"

echo "3. 第一个服务慢（12 秒）、第二个快（2 秒）：第一个要是被丢掉，就会在 2 秒左右返回"
docker exec "${PREFIX}-a" rm -f /tmp/ready; docker exec "${PREFIX}-b" rm -f /tmp/ready
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  first="$(docker inspect --format '{{.State.Health.Status}}' "${PREFIX}-a")"
  second="$(docker inspect --format '{{.State.Health.Status}}' "${PREFIX}-b")"
  [ "$first" != healthy ] && [ "$second" != healthy ] && break
  sleep 1
done
ready_later "${PREFIX}-a" 12; ready_later "${PREFIX}-b" 2
SECONDS=0; PROJECT="yunnan-wait" TIMEOUT=60 wait_healthy probe-a probe-b; code=$?
[ $code -eq 0 ] && [ "$SECONDS" -ge 11 ]; check $? "第一个与第二个服务都等到了（返回码 $code，用时 ${SECONDS}s）"

echo "4. 容器退出"
start "${PREFIX}-dead"; docker exec "${PREFIX}-dead" touch /tmp/ready; sleep 3
docker stop "${PREFIX}-dead" >/dev/null
SECONDS=0; PROJECT="yunnan-wait" TIMEOUT=60 wait_healthy probe-dead; code=$?
[ $code -ne 0 ] && [ "$SECONDS" -le 12 ]; check $? "退出的容器立即报失败（返回码 $code，用时 ${SECONDS}s）"

echo
echo "$failures 项失败"
exit $([ "$failures" -eq 0 ] && echo 0 || echo 1)
