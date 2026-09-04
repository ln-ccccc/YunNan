# Docker 调试与热修指令

> **已归档，禁止用于当前云南部署。** 下方命令仅保留历史记录，请勿执行；当前离线部署入口见 [`docs/offline_deployment_guide.md`](offline_deployment_guide.md)，当前重启与排障见 [`docs/docker_restart_guide.md`](docker_restart_guide.md)。

以下内容仅记录旧 Docker 部署方式：`docker-compose.prod.yml`、`cugrs-app`、`cugrs-mysql`。

## 1. 查看状态

```bash
cd /path/to/GeoView
docker compose -f docker-compose.prod.yml ps
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
```

## 2. 查看日志

```bash
docker logs --tail 200 cugrs-app
docker logs --tail 120 cugrs-mysql
docker logs -f --tail 200 cugrs-app
```

## 3. 进入容器排查

```bash
docker exec -it cugrs-app bash
docker exec -it cugrs-mysql bash
```

容器内常用检查：

```bash
cd /app
python -V
node -v
ls -lah /app/backend
ls -lah /app/frontend
ls -lah /app/miner
ls -lah /offline_maps/dali
echo "$MINER_TILE_TIF_PATH"
```

## 4. HTTP 健康检查

```bash
curl -I http://127.0.0.1:3000/
curl -I http://127.0.0.1:4000/
curl -I http://127.0.0.1:5008/
curl -I http://127.0.0.1:8000/api/stats
curl -I http://127.0.0.1:8000/tiles/5/24/13.png
```

说明：

- `http://127.0.0.1:8000/` 返回 `404` 是正常现象。
- `/tiles/5/24/13.png` 返回 `200 image/png` 才表示离线底图链路正常。

## 5. 只重启应用容器

```bash
docker compose -f docker-compose.prod.yml up -d --force-recreate app
docker logs --tail 200 cugrs-app
```

## 6. 容器名冲突

```bash
docker rm -f cugrs-app cugrs-mysql
docker compose -f docker-compose.prod.yml up -d --remove-orphans
```

## 7. 底图灰底排查

先确认宿主机目录存在数据：

```bash
ls -lah offline_bundle/maps/dali
```

再用正确变量重启：

```bash
export APP_IMAGE=geoview-runtime:current
export MYSQL_IMAGE=registry.openanolis.cn/openanolis/mysql:8.0.30-8.6
export OFFLINE_MAP_DIR="$(pwd)/offline_bundle/maps/dali"
export MINER_TILE_TIF_PATH="/offline_maps/dali/大理白族自治州_卫图1_Level_15.tif"
export MINER_MAP_PROVIDER=local
export MINER_LOCAL_TILE_URL='http://localhost:8000/tiles/{z}/{x}/{y}.png'

docker compose -f docker-compose.prod.yml up -d --force-recreate app
curl -I http://127.0.0.1:8000/tiles/5/24/13.png
```

## 8. 临时热修：覆盖容器内文件

适合快速验证问题。容器重建后会丢失，需要再固化到源码或镜像。

```bash
docker cp backend/applications/api/analysis.py cugrs-app:/app/backend/applications/api/analysis.py
docker compose -f docker-compose.prod.yml up -d --force-recreate app
docker logs --tail 200 cugrs-app
```

## 9. 离线包热修：源码重建镜像

在 `offline_bundle` 目录执行：

```bash
cd offline_bundle
mkdir -p hotfix_src
tar -xzf source/GeoView_source_*.tar.gz -C hotfix_src
./hotfix_rebuild.sh
```

`hotfix_rebuild.sh` 会读取 `image_bundle.env`，当前默认重建镜像：

```text
geoview-runtime:current
```

不要手动改成 `geoview:latest`，否则 `docker-compose.prod.yml` 默认不会使用新镜像。
