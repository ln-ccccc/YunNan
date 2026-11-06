# 备份与恢复（2026-11-05 起）

## 备份

```bash
bash docker/backup_yunnan.sh                       # 常规（不含模型权重、不含可再生目录）
bash docker/backup_yunnan.sh --with-regenerable    # 额外含 basemaps 源副本 + tiles 瓦片
bash docker/backup_yunnan.sh --with-model          # 额外含 9.5GB 权重
bash docker/backup_yunnan.sh --keep 5              # 保留最近 5 份（默认 3）
```

产物在 `backups/yunnan_backup_<时间戳>/`：MySQL 逻辑备份（gzip）、
`yunnan_backend_static` / `yunnan_miner_uploads` / `yunnan_miner_outputs` 三卷 tar、
`project_storage` tar、`manifest.json`（git sha + 每个产物 sha256）。

**默认排除（2026-11-06 首次实测 45GB 工作区后定的取舍）**：
- `projects/*/basemaps/`——底图源副本，原件在主树 `maps/` 或 `incoming/`
- `projects/*/tiles/`——瓦片金字塔，可由源影像重切恢复
- `trash/`——已删项目暂存（实测 18GB），误删恢复走 trash API 三步流程，不靠备份
- `incoming/dali`——离线底图 bind，主树 `maps/` 有原件
- `yunnan_hf_cache`（可重建）、`yunnan_inference_runtime`（任务暂存）

排除后单份约 12GB 量级（大头是上传原始影像卷 3.2GB + inputs 副本 + DB 485MB）；
含 `--with-regenerable` 单份再增 20GB+，仅在源影像将不再保留时使用。
**恢复默认备份后，basemap 需从 maps/ 重新导入、瓦片需重切**——恢复演练请把这一步计入时间。
活栈运行时 tar 可能报"file changed as we read it"并提示告警（脚本已排除健康探针；
产物仍可用），追求严格时点一致请在业务静默期执行。

建议频率：每周一次 + 大批量数据导入/删除前临时一次。**送测/迁移前必做一次。**

## 恢复（灾难恢复 / 换机）

按顺序执行，恢复 = 覆盖，操作前先停写入侧：

1. **停栈**：`docker compose -f docker-compose.prod.yml down`（数据卷保留）
2. **MySQL**：
   ```bash
   gunzip -c backups/<目录>/mysql_paddle_rs.sql.gz | \
     MSYS_NO_PATHCONV=1 docker exec -i yunnan-mysql sh -c \
     'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" paddle_rs'
   ```
   （要求 mysql 容器已 `up -d` 单独启动）
3. **命名卷**（以 miner_uploads 为例，其余同理）：
   ```bash
   MSYS_NO_PATHCONV=1 docker run --rm --entrypoint sh \
     -v yunnan_miner_uploads:/dst -v "$(pwd)/backups/<目录>:/src" yunnan-runtime:current \
     -c "cd /dst && tar -xzf /src/yunnan_miner_uploads.tar.gz"
   ```
4. **project_storage**：`tar -xzf backups/<目录>/project_storage.tar.gz -C project_storage`
5. **校验**：对每个产物跑 `sha256sum` 与 manifest 比对，再 `docker compose up -d`
6. **演练**：恢复后登录 → 任一项目 overview/资产/瓦片可读 → 跑一次导出

## 已知边界

- MySQL dump 与卷 tar 非同一瞬间快照（分钟级窗口）：恢复后以数据库记录为准，
  引用已消失文件的资产会显示 failed/missing，重新导入即可
- 模型权重默认不打（离线迁移走 deploy_offline 流程，其清单含权重）
