# 当前项目感知的解译路由验证记录

日期：2026-08-20

## 验证范围

- 浏览器 CORS 预检不再被会话鉴权拦截，实际推理请求仍要求登录。
- Miner 进入 GeoView 地物分类、光谱指数时携带当前 `project_id`。
- 后端只读取当前项目最新的活动矿山矢量和绑定 FID。
- TIFF 有效像元命中当前项目矿山时，分类结果、变化矩阵和光谱指数写入当前项目目录。
- 未命中、无项目或 TIFF 缺少 CRS 时只走 GeoView 独立展示，不写项目矿山数据。
- 当前项目历史只列出当前项目绑定 FID 的结果。

## 自动化验证

后端相关回归：

```text
docker exec yunnan-backend bash -lc "cd /app/backend && python -m unittest test_auth_api test_inference_jobs test_inference_runner test_inference_routing test_interpretation_api test_kml_roi_pipeline test_project_inference_results test_project_map test_spectral_indices"
Ran 80 tests ... OK
```

Miner：

```text
npm test
37 passed, 0 failed

npm run build
exit 0
```

GeoView：

```text
node --test frontend/test 下全部 *.test.mjs
7 passed, 0 failed

npm run build
exit 0
```

Python 源码额外使用临时字节码目录执行 `compileall`，退出码为 0。源码在生产容器中按只读方式挂载，因此不能把 `__pycache__` 写回源码目录。

## 运行环境验证

- `GET http://127.0.0.1:3000/segmentation?project_id=1`：200
- `OPTIONS /api/analysis/kml_roi_inference`：200，允许来源 `http://127.0.0.1:3000`
- 未登录 `POST /api/analysis/kml_roi_inference`：401
- Miner Web、Miner API、GeoView Web、GeoView API：均返回 200
- `yunnan-backend`、`yunnan-frontend`、`yunnan-miner-api`、`yunnan-miner-web`、`yunnan-mysql`：healthy
- `yunnan-inference-worker`：healthy；`yunnan-spatial-worker`：running

## 已知限制

- 本轮未使用真实业务 TIFF 提交一次完整 MMSeg 模型任务，以免在没有确认测试影像和目标项目的情况下产生业务输出；模型任务编排、发布和隔离由自动化测试覆盖。
- Codex 本地浏览器连接受到用户目录权限限制，未完成自动点击上传的 UI 验证；已完成页面 HTTP、CORS、鉴权、前端构建和请求参数的自动化验证。
- 构建仍有既有的 bundle 体积、Browserslist 数据较旧和 Vue 深度选择器弃用警告，本次未扩展范围处理。
- 当前目录不是有效 Git 仓库，因此没有生成提交或分支。
