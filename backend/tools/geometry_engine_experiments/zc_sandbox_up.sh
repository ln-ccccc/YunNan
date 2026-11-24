#!/usr/bin/env bash
# 隔离沙盒栈（不碰生产容器、不碰生产库）：
#   临时 MySQL（只复制编辑器需要的表：全部小表 + 沙盒项目的成果 / 修订 / 推理任务）
#   + 隔离后端（定稿 commit 的干净导出，只读挂载；连接临时库；宿主端口 127.0.0.1:5018）
# 用法: bash zc_sandbox_up.sh [沙盒项目 id，默认 52]
# 任一步失败立即中止并保留错误输出——不允许「静默成功但数据没复制过来」。
set -euo pipefail
REPO="D:/项目/YunNan"
WORK="${ZC_GEOM_WORK:-D:/YunNan-Handoff/zcode-20261124-geometry}"  # 数据导出、输出、commit 导出都放在仓库外
COMMIT_FULL="$(git -C "$REPO" rev-parse 36103e8)"
SRC="$WORK/src-${COMMIT_FULL:0:12}"
NET="zc-geom-net"
ENV_FILE="$WORK/zc-geom.env"
PROJECT_ID="${1:-52}"
export MSYS_NO_PATHCONV=1
test -f "$SRC/backend/applications/project_hub/geometry_engine.py" || { echo "缺少 commit 导出目录: $SRC"; exit 1; }

docker network create "$NET" >/dev/null 2>&1 || true
ROOT_PW="$(openssl rand -hex 16)"
APP_PW="$(openssl rand -hex 16)"
APP_USER="$(docker inspect yunnan-backend --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^MYSQL_USERNAME=' | cut -d= -f2-)"

docker rm -f -v zc-geom-mysql zc-geom-backend >/dev/null 2>&1 || true
docker run -d --name zc-geom-mysql --network "$NET" \
  -e MYSQL_ROOT_PASSWORD="$ROOT_PW" -e MYSQL_DATABASE=paddle_rs -e MYSQL_USER="$APP_USER" -e MYSQL_PASSWORD="$APP_PW" \
  registry.openanolis.cn/openanolis/mysql:8.0.30-8.6 >/dev/null
echo "临时 MySQL 已启动，等待就绪"
# 镜像初始化时会先起一个只走本地套接字的临时实例（ping 那时就成功，但口令还没生效），
# 所以必须用口令走 TCP 真正查一次才算就绪
ready=0
for attempt in $(seq 1 90); do
  if docker exec zc-geom-mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -h127.0.0.1 -uroot -N -e "SELECT 1"' >/dev/null 2>&1; then ready=1; break; fi
  sleep 2
done
[ "$ready" = 1 ] || { echo "临时 MySQL 未就绪"; exit 1; }

# 口令通过容器内的环境变量传递，不出现在命令行里，也不落盘
prod_dump() { docker exec yunnan-mysql sh -c "MYSQL_PWD=\"\$MYSQL_ROOT_PASSWORD\" mysqldump --single-transaction --quick --no-tablespaces -uroot $1"; }
temp_load() { docker exec -i zc-geom-mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot paddle_rs'; }
temp_sql() { docker exec zc-geom-mysql sh -c "MYSQL_PWD=\"\$MYSQL_ROOT_PASSWORD\" mysql -uroot -N -e \"$1\" paddle_rs"; }
copy() { echo "  复制: $1"; prod_dump "$2" | temp_load; }
echo "复制数据（流式，不落盘）"
copy "全部小表（含用户、项目、矿山、资源、数据集）" \
  "paddle_rs --ignore-table=paddle_rs.classification_results --ignore-table=paddle_rs.inference_jobs --ignore-table=paddle_rs.classification_revisions"
copy "三张大表的结构" "--no-data paddle_rs classification_results inference_jobs classification_revisions"
copy "沙盒项目的成果" "--no-create-info --skip-triggers paddle_rs classification_results --where=project_id=$PROJECT_ID"
copy "沙盒项目成果的修订" "--no-create-info --skip-triggers paddle_rs classification_revisions --where='result_id IN (SELECT id FROM classification_results WHERE project_id=$PROJECT_ID)'"
copy "沙盒项目的推理任务" "--no-create-info --skip-triggers paddle_rs inference_jobs --where=project_id=$PROJECT_ID"
RESULTS="$(temp_sql 'SELECT COUNT(*) FROM classification_results')"
REVISIONS="$(temp_sql 'SELECT COUNT(*) FROM classification_revisions')"
PROJECTS="$(temp_sql 'SELECT COUNT(*) FROM project')"
echo "临时库：projects=$PROJECTS results=$RESULTS revisions=$REVISIONS"
[ "$RESULTS" -ge 1 ] && [ "$REVISIONS" -ge "$RESULTS" ] || { echo "复制结果不完整（成果 $RESULTS 条、修订 $REVISIONS 条），中止"; exit 1; }

docker inspect yunnan-backend --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -v -E '^(MYSQL_HOST|MYSQL_PORT|MYSQL_PASSWORD|FRONTEND_PORT|WEB_CONCURRENCY|WEB_THREADS|YUNNAN_GIT_SHA)=' > "$ENV_FILE"
printf 'MYSQL_HOST=zc-geom-mysql\nMYSQL_PORT=3306\nMYSQL_PASSWORD=%s\nFRONTEND_PORT=4403\nWEB_CONCURRENCY=1\nYUNNAN_GIT_SHA=%s\n' "$APP_PW" "${COMMIT_FULL:0:12}" >> "$ENV_FILE"
echo "环境文件含密钥，仅用于本次沙盒：$ENV_FILE（收尾时删除）"

docker run -d --name zc-geom-backend --network "$NET" -p "127.0.0.1:5018:5008" --env-file "$ENV_FILE" \
  --entrypoint bash -w /app \
  -v "$SRC/backend:/app/backend:ro" \
  -v "$REPO/config.yaml:/app/config.yaml:ro" \
  -v "$REPO/project_storage:/project_storage" \
  -v "$REPO/maps/dali:/project_storage/incoming/dali:ro" \
  -v "$REPO/miner/yunnan.kml:/app/miner/yunnan.kml:ro" \
  -v "zc-geom-outputs:/app/miner/change_matrix_outputs" \
  -v "zc-geom-uploads:/app/miner/uploads" \
  yunnan-runtime:current -c "source /opt/conda/etc/profile.d/conda.sh && conda activate MMSeg310 && cd /app/backend && exec gunicorn --bind 0.0.0.0:5008 --workers 1 --threads 4 --timeout 120 app:app" >/dev/null
echo "隔离后端已启动（commit ${COMMIT_FULL:0:12}），等待就绪"
code=000
for attempt in $(seq 1 45); do
  code="$(curl -s -o /dev/null -w '%{http_code}' http://localhost:5018/api/auth/session || true)"
  if [ "$code" = "200" ] || [ "$code" = "401" ]; then break; fi
  sleep 2
done
echo "backend http=$code"
[ "$code" = "200" ] || [ "$code" = "401" ] || { docker logs --tail 30 zc-geom-backend 2>&1 | cut -c1-200; exit 1; }
docker ps --filter name=zc-geom --format '{{.Names}} {{.Status}} {{.Ports}}'
