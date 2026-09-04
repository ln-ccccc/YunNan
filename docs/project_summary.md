# GeoView 项目概览

## 1. 系统组成

- GeoView 前端：Vue + Element Plus，端口 `3000`
- GeoView 后端：Flask/Python，端口 `5008`
- Miner 前端：Vue/Vite，端口 `4000`
- Miner 后端：Node/Express，端口 `8000`
- 推理 Worker：Python/MMSeg，服务名 `inference-worker`
- 空间 Worker：Python/GDAL，服务名 `spatial-worker`
- MySQL：容器内 `3306`，默认不映射到宿主端口

## 2. 当前主要功能

- 地表覆盖分类：上传 GeoTIFF，执行 KML ROI 推理，结果同步到 Miner 输出目录。
- 光谱指数计算：支持 NDVI、NDWI、NDBI、NDSI，生成统计结果、预览图和历史记录。
- Miner 展示：读取矿区 KML、变化矩阵输出和指数数据，提供地图展示、趋势统计和导出。
- 离线底图：宿主机 `maps/dali` 由 Compose 只读挂载到 `/project_storage/incoming/dali`；后端从 `project_storage/incoming` 递归扫描 `.tif`/`.tiff`，生成项目级本地瓦片，文件名和编码不作为契约。

## 3. 当前部署方式

生产/离线部署统一使用：

```text
docker-compose.prod.yml
APP_IMAGE=yunnan-runtime:current
INFERENCE_IMAGE=yunnan-inference-worker:current
MYSQL_IMAGE=registry.openanolis.cn/openanolis/mysql:8.0.30-8.6
```

关键底图变量：

```text
OFFLINE_MAP_DIR=/path/to/GeoView/maps/dali
```

## 4. 关键验证点

- `http://localhost:3000/`：GeoView 前端
- `http://localhost:4000/`：Miner 前端
- `http://localhost:5008/api/auth/session`：GeoView 后端认证会话接口
- `http://localhost:8000/api/auth/session`：Miner 后端认证会话接口
- 项目 manifest：已认证请求 `GET http://localhost:8000/api/projects/{projectId}/map/manifest`（需 Cookie）读取实际资源信息。
- 项目瓦片：先登录并从 manifest 读取实际 `projectId`、`resourceId`、`z/x/y`，再请求 `http://localhost:8000/tiles/projects/{projectId}/{resourceId}/{z}/{x}/{y}.png`

`http://localhost:8000/` 根路径返回 `404` 是正常现象。
