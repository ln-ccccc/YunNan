#!/usr/bin/env bash
# 核对运行中的栈跑的就是这份发布目录。只读：不重启、不改配置，任何一项不符即非零退出并写明差在哪。
#
# 用法：deploy/verify_release.sh <发布目录> [--project yunnan] [--skip 服务,...] [--env-file .env] [--backend-url 地址] ...
#   选项含义同 switch_release.sh；默认核对生产（项目 yunnan、端口 5008 / 3000 / 8000 / 4000、环境文件 .env）。
#
# 核对内容：
#   发布目录自身      文件指纹与 RELEASE.json 一致、依赖与锁文件一致（目录生成后没有人动过）
#   每个应用容器      源码挂载指向发布目录且只读、容器内代码指纹与清单一致、容器启动时间不早于发布目录、
#                    健康检查通过、镜像编号与清单记的是同一个
#   接口              后端健康、版本自述等于发布目录的提交、三个几何接口存在且要求登录、GeoView 是带后端校验的构建
#   环境文件          .env 里钉住的源码目录与版本就是这一份（否则旧习惯的 up -d 会把容器拉回开发树）
set -euo pipefail
export MSYS_NO_PATHCONV=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if command -v cygpath >/dev/null 2>&1; then
  TOOL="$(cygpath -m "$HERE/release_tool.py")"
else
  TOOL="$HERE/release_tool.py"
fi

ENV_ARGS=()
case " $* " in
  *" --env-file "*) ;;
  *) [ -f .env ] && ENV_ARGS=(--env-file .env) || true ;;
esac
exec python "$TOOL" verify "$@" ${ENV_ARGS[@]+"${ENV_ARGS[@]}"}
