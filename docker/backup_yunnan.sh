#!/usr/bin/env bash
# 云南栈备份脚本（2026-11-05 生产化检查，迁移审查 P2-16）
# 在宿主机运行（Git Bash / Linux 均可），产物落到 ./backups/yunnan_backup_<时间戳>/
#
# 覆盖：
#   1. MySQL 逻辑备份（--single-transaction，InnoDB 一致性快照，不需停库）
#   2. 命名卷 yunnan_backend_static / yunnan_miner_uploads / yunnan_miner_outputs
#      （经 yunnan-runtime 镜像打包，离线环境无 busybox 可拉）
#   3. project_storage 主树 bind 目录（项目工作区/成果/exports）
#   4. manifest.json（时间戳/git sha/各产物 sha256，恢复与对账依据）
# 不含：hf_cache（可重建缓存）、inference_runtime（任务暂存）、模型权重
#       （9.5GB，加 --with-model 打包 backend/model/，离线迁移时建议用
#       deploy_offline 流程而非本脚本）
#
# 用法：bash docker/backup_yunnan.sh [--with-model] [--keep N]
# 恢复：见 docs/backup-restore.md

set -euo pipefail

cd "$(dirname "$0")/.."

KEEP=5
WITH_MODEL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-model) WITH_MODEL=1; shift ;;
    --keep) KEEP="$2"; shift 2 ;;
    *) echo "未知参数: $1（支持 --with-model / --keep N）" >&2; exit 2 ;;
  esac
done

STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="backups/yunnan_backup_${STAMP}"
MYSQL_CONTAINER="${MYSQL_CONTAINER:-yunnan-mysql}"
RUNTIME_IMAGE="${APP_IMAGE:-yunnan-runtime:current}"

mkdir -p "${OUT}"

echo "[backup] 1/4 MySQL 逻辑备份（paddle_rs）"
MSYS_NO_PATHCONV=1 docker exec "${MYSQL_CONTAINER}" sh -c \
  'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --triggers paddle_rs' \
  | gzip > "${OUT}/mysql_paddle_rs.sql.gz"

echo "[backup] 2/4 命名卷打包（backend_static / miner_uploads / miner_outputs）"
for vol in yunnan_backend_static yunnan_miner_uploads yunnan_miner_outputs; do
  MSYS_NO_PATHCONV=1 docker run --rm --entrypoint sh \
    -v "${vol}:/src:ro" -v "$(pwd)/${OUT}:/dst" "${RUNTIME_IMAGE}" \
    -c "tar -czf /dst/${vol}.tar.gz -C /src ."
done

echo "[backup] 3/4 project_storage 打包（不含 incoming/dali 离线底图 bind，其在主树 maps/ 有原件）"
tar --exclude='./incoming/dali' -czf "${OUT}/project_storage.tar.gz" -C project_storage .

if [[ "${WITH_MODEL}" == "1" ]]; then
  echo "[backup] 附带模型权重（backend/model，9.5GB 量级，耗时较长）"
  tar -czf "${OUT}/model_weights.tar.gz" -C backend model
fi

echo "[backup] 4/4 manifest"
GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
{
  printf '{\n  "created_at": "%s",\n  "git_sha": "%s",\n  "artifacts": [\n' \
    "$(date -Iseconds)" "${GIT_SHA}"
  first=1
  for f in "${OUT}"/*.gz; do
    [[ "${first}" == "1" ]] || printf ',\n'
    first=0
    printf '    {"file": "%s", "bytes": %s, "sha256": "%s"}' \
      "$(basename "${f}")" "$(stat -c %s "${f}" 2>/dev/null || wc -c < "${f}")" \
      "$(sha256sum "${f}" | cut -d' ' -f1)"
  done
  printf '\n  ]\n}\n'
} > "${OUT}/manifest.json"

echo "[backup] 轮转保留最近 ${KEEP} 份"
mapfile -t expired < <(ls -1d backups/yunnan_backup_* 2>/dev/null | head -n -"${KEEP}" || true)
for old in "${expired[@]:-}"; do
  [[ -z "${old}" ]] && continue
  echo "[backup] 删除过期备份: ${old}"
  rm -rf "${old}"
done

echo "[backup] 完成: ${OUT}"
du -sh "${OUT}"/* | sed 's/^/[backup]   /'
