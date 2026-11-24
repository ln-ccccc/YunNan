#!/usr/bin/env bash
# 拆除隔离沙盒栈：停掉并删除临时容器、匿名卷、临时卷、临时网络，删除含密钥的环境文件。
set -uo pipefail
export MSYS_NO_PATHCONV=1
WORK="${ZC_GEOM_WORK:-D:/YunNan-Handoff/zcode-20261124-geometry}"  # 数据导出、输出、commit 导出都放在仓库外
docker rm -f -v zc-geom-backend zc-geom-mysql >/dev/null 2>&1
docker volume rm zc-geom-outputs zc-geom-uploads >/dev/null 2>&1
docker network rm zc-geom-net >/dev/null 2>&1
rm -f "$WORK/zc-geom.env"
echo "remaining: $(docker ps -a --filter name=zc-geom --format '{{.Names}}' | tr '\n' ' ')| volumes: $(docker volume ls -q --filter name=zc-geom | tr '\n' ' ')| env file: $([ -f "$WORK/zc-geom.env" ] && echo still-there || echo removed)"
