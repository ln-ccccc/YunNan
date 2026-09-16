# 旧版数据迁移说明

## 目的

`backend/migrate_legacy_project_data.py` 用于把旧版全局成果补录到项目工作台，包括：

- 历史矿山绑定
- 历史变化矩阵输出目录
- 历史分析记录
- 缺失的 `NDBI` / `NDSI` 指数工作簿

迁移逻辑只做 upsert，不删除现有项目、绑定和数据集。

## 运行前提

真实历史成果不在 `backend` 工作树里，而在 Docker 运行态：

- `geoview_miner_outputs` 卷：历史 `change_matrix_outputs`
- `geoview_backend_static` 卷：历史上传/结果图
  （这两个 `geoview_*` 卷是隔离前历史数据的物理所在，仅作一次性迁移读取来源；
  日常云南运行一律使用 `yunnan_*` 卷，见 docs/offline_deployment_guide.md 隔离边界）
- MySQL：项目元数据与旧版 `analysis` 记录

因此执行迁移时，必须让脚本同时看到：

- `/app/backend`
- `/app/miner`
- `/app/miner/change_matrix_outputs`
- `/app/backend/static`

## 推荐执行方式

在和当前容器相同的 Docker 网络里启动一次性容器执行：

```powershell
docker run --rm --entrypoint /bin/sh --network yunnan_default `
  -e MYSQL_HOST=mysql `
  -e MYSQL_PORT=3306 `
  -e MYSQL_USERNAME=paddle_rs `
  -e MYSQL_PASSWORD=${MYSQL_PASSWORD} `
  -e MYSQL_DATABASE=paddle_rs `
  -e FLASK_CONFIG=production `
  -e SECRET_KEY=<your-secret-key> `
  -e ADMIN_USERNAME=admin `
  -e ADMIN_PASSWORD=<your-admin-password> `
  -v "D:/项目/YunNan/backend:/app/backend" `
  -v "D:/项目/YunNan/miner:/app/miner" `
  -v geoview_miner_outputs:/app/miner/change_matrix_outputs `
  -v geoview_backend_static:/app/backend/static `
  yunnan-runtime:current `
  -lc "cd /app/backend && python migrate_legacy_project_data.py --project-name 历史成果迁移项目 --manager admin"
```

## 迁移后检查

- 项目列表中应出现 `历史成果迁移项目`
- `miner/` 下应存在 `NDBI_by_fid_2year_avg.xlsx` 与 `NDSI_by_fid_2year_avg.xlsx`
- `GET /api/projects` 返回的历史项目应包含迁移后的矿山数和数据集数
- `GET /api/mines/indices?fid=<fid>` 不应再因为缺少 `NDBI/NDSI` 工作簿而直接报缺文件
