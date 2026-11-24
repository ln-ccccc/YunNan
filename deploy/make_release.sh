#!/usr/bin/env bash
# 从一个固定的 Git 提交生成发布目录：容器要挂载的源码，加上 GeoView 按锁文件装好的依赖，并写入 RELEASE.json。
#
# 用法：deploy/make_release.sh <commit> [发布根目录]
#   <commit>       提交号、分支名或标签。只导出已提交的内容，工作区里未提交的改动不会进发布目录
#   发布根目录     默认是仓库上一级的 yunnan-releases；成品目录名是 <短提交号>-<UTC 时间>
#
# 宿主需要：git、tar、node / npm（装 GeoView 依赖）、python 3（记账）、docker（记镜像编号）。
# 第一次装依赖要能访问 npm 源（或本机已有 npm 缓存）。每个发布目录约 0.4 GiB，磁盘要留出 2 GiB。
# 推理权重等运行时资产（约 9.7GB，不入库）不进发布目录：它们留在 YUNNAN_ASSET_ROOT 指向的目录
# （默认是开发树），compose 单独挂进容器，清单里只记大小、文件数和 SHA256。第一次要算几分钟，之后走缓存。
# 只读仓库与镜像，不改 .env，不碰运行中的容器。发布目录生成后不要再改，要换内容就重新生成一份。
set -euo pipefail
export MSYS_NO_PATHCONV=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${YUNNAN_REPO:-$(cd "$HERE/.." && pwd)}"
TOOL="$HERE/release_tool.py"

# 交给 Windows 原生程序（git、python、npm、docker）的路径一律用 D:/... 形式
native() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi
}
NATIVE_REPO="$(native "$REPO")"
NATIVE_TOOL="$(native "$TOOL")"

COMMIT_ARG="${1:?用法: deploy/make_release.sh <commit> [发布根目录]}"
if [ $# -gt 1 ]; then
  RELEASES="$(native "$2")"
else
  RELEASES="$(native "$REPO/../yunnan-releases")"
fi
case "$RELEASES/" in
  "$NATIVE_REPO"/*) echo "发布根目录不能放在仓库里面：$RELEASES" >&2; exit 2 ;;
esac
mkdir -p "$RELEASES"
DATA_DIR="${YUNNAN_DATA_DIR:-$NATIVE_REPO}"

COMMIT="$(git -C "$NATIVE_REPO" rev-parse --verify --end-of-options "${COMMIT_ARG}^{commit}")"
SHORT="${COMMIT:0:7}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="${SHORT}-${STAMP}"
STAGING="$RELEASES/.staging-$NAME"
FINAL="$RELEASES/$NAME"
[ ! -e "$FINAL" ] || { echo "已经存在：$FINAL" >&2; exit 2; }

cleanup() {
  if [ -d "$STAGING" ] && [ "$(basename "$STAGING")" = ".staging-$NAME" ]; then
    rm -rf -- "$STAGING"
  fi
}
trap cleanup EXIT

FREE_BYTES="$(python -c 'import shutil, sys; print(shutil.disk_usage(sys.argv[1]).free)' "$RELEASES" | tr -d '\r')"
[[ "$FREE_BYTES" =~ ^[0-9]+$ ]] || { echo "读不到 $RELEASES 的磁盘剩余空间" >&2; exit 2; }
if [ "$FREE_BYTES" -lt $((2 * 1024 * 1024 * 1024)) ]; then
  echo "磁盘剩余 $((FREE_BYTES / 1024 / 1024)) MiB，不足 2 GiB：$RELEASES" >&2
  exit 2
fi

# 运行时资产（不入库的权重与随附的第三方源码）不进发布目录，只记账；默认在开发树里
ASSET_ROOT="$(native "${YUNNAN_ASSET_ROOT:-$REPO}")"

echo "[0/4] 开发树里未提交的条目逐个归类"
python "$NATIVE_TOOL" untracked-check --repo "$NATIVE_REPO" --asset-root "$ASSET_ROOT"

echo "[1/4] 导出 $SHORT 的源码到 $STAGING"
mapfile -t ARCHIVE_PATHS < <(python "$NATIVE_TOOL" trees | tr -d '\r')
mkdir "$STAGING"
git -C "$NATIVE_REPO" archive --format=tar "$COMMIT" -- "${ARCHIVE_PATHS[@]}" | tar -x -C "$STAGING"
# 命名卷和运行时资产的挂载点：只读绑定里建不了目录，发布目录里必须预先留好
python "$NATIVE_TOOL" placeholders "$STAGING"

echo "[2/4] 按 package-lock.json 安装 GeoView 依赖"
export RELEASE_NODE_VERSION="$(node --version)"
export RELEASE_NPM_VERSION="$(npm --version)"
if ! (cd "$STAGING/frontend" && npm ci --no-audit --no-fund --prefer-offline --loglevel=error); then
  echo "依赖安装失败。发布目录没有生成。先确认能访问 npm 源（npm config get registry），再重试。" >&2
  exit 1
fi

echo "[3/4] 记账"
# 镜像编号按 compose 实际使用的引用来记；环境里没给时取 image_bundle.env 里的（该文件入库、不含口令）
for key in APP_IMAGE INFERENCE_IMAGE MYSQL_IMAGE; do
  if [ -z "${!key:-}" ] && [ -f "$NATIVE_REPO/image_bundle.env" ]; then
    value="$(grep -E "^${key}=" "$NATIVE_REPO/image_bundle.env" | tail -n 1 | cut -d= -f2- | tr -d '\r"')"
    [ -n "$value" ] && export "$key=$value"
  fi
done
echo "[3/4] 记账"
python "$NATIVE_TOOL" manifest "$STAGING" --commit "$COMMIT" --repo "$NATIVE_REPO" --data-dir "$DATA_DIR" --name "$NAME" \
  --asset-root "$ASSET_ROOT" --asset-cache "$RELEASES/.asset-fingerprints.json"

echo "[4/4] 发布目录就位"
mv "$STAGING" "$FINAL"
trap - EXIT

SUBJECT="$(git -C "$NATIVE_REPO" log -1 --format=%s "$COMMIT")"
cat <<SUMMARY

发布目录：$FINAL
提交：$COMMIT《$SUBJECT》
下一步（会重启容器，须负责人确认后执行）：
  deploy/switch_release.sh $FINAL
切换前建议先生成回滚用的那份：deploy/make_release.sh <回滚到的提交>
SUMMARY
