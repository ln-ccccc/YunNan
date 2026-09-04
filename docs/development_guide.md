# 云南矿山监测项目开发文档与规范

## 1. 项目结构

- `miner/`：矿山监测主系统，包含 Vite/Vue 前端与 `server.js` 提供的 Miner API。
- `backend/`：Python/Flask 与遥感解译、KML ROI、光谱指数等算法服务。
- `frontend/`：旧 GeoView 前端。除非任务明确要求，不在 miner 优化任务中重构该目录。
- `docs/`：开发、部署、测试与运行文档。

## 2. 服务与端口

- Miner Web：`miner` 的 Vite 开发服务，默认 `http://localhost:4000/`。
- Miner API：`miner/server.js`，默认 `http://localhost:8000/`。
- 后端 Flask：以 `backend` 现有启动脚本为准，供 GeoView 或算法服务调用。

如端口被占用，应优先改本地启动参数，不要直接修改接口契约或硬编码端口。

## 3. 常用命令

```powershell
cd miner
npm install
npm test
node --check server.js
npm run build
```

```powershell
cd frontend
npm install
npm run build
```

```powershell
cd backend
python -m unittest test_spectral_indices.py
python -m unittest test_new_features.py
```

## 4. Miner API 契约

### `POST /api/kml/upload`

Request:

```json
{
  "filename": "example.kml",
  "content": "<kml>...</kml>"
}
```

Success:

```json
{
  "kml_path": "D:\\项目\\YunNan\\miner\\uploads\\kml\\example.kml"
}
```

Failure:

- 非 `.kml` 文件：`400`
- 空内容：`400`
- 保存失败：`400`

错误响应应包含可读的 `error`，并尽量提供 `next` 排查建议。

### `POST /api/inference/kml-roi`

保留现有字段，并支持：

- `year`：单年份推理参数。
- `old_year`：基准年份。
- `new_year`：最新年份。

当 `year` 存在时，优先使用单年份模式；否则传递 `old_year/new_year`。

### `GET /api/mines/trend-report`

Query:

- `class_name=forest|grassland|building|road|bareground|water`
- `direction=upward|downward|stable|all`

Response 至少包含：

- `mine_total`
- `coverage`
- `available_classes`
- `filters`
- `class_trends.selected_class`
- `tables.selected_class_rows`

趋势统计必须来自真实 `class_ratio_percent.json` 等结果文件；无数据时返回空表和覆盖率信息，不伪造全 0 趋势。

## 5. 前端规范

- 主界面文案默认使用中文，按钮文案必须与实际行为一致，例如导出 CSV 时写“导出 CSV”。
- 页面级失败必须可见：数据加载失败、趋势统计失败、推理失败不能静默降级为 0 数据。
- 删除矿山等高风险入口只有在后端接口真实支持并经过验证后才允许展示。
- 地图首次加载可自动缩放到全量边界；筛选刷新不应反复打断用户视角，搜索命中时只定位目标矿山。

## 6. 编码边界

- 优先复用现有目录和组件，不做无关目录重构。
- 不为“未来可能需要”新增配置中心、插件机制或抽象层。
- 不批量格式化无关文件。
- 修改 API、配置、命令或部署流程时必须同步更新文档。
- 错误信息至少说明失败原因、失败位置或下一步排查方向。

## 7. 测试分层

- Node 单元测试：覆盖纯函数、接口参数构造、文件保存校验、趋势统计计算。
- 静态检查：`node --check server.js`。
- 构建测试：`npm run build`。
- Python 单元测试：运行 `backend` 现有 unittest。
- API 集成测试：启动服务后验证 KML 上传、趋势报告、基础 stats。
- UI 检查：首页、趋势弹窗、推理弹窗、筛选/搜索/重置、错误态。

## 8. 交付要求

每次开发完成后必须列出：

- 改了什么。
- 为什么这样改。
- 实际执行的测试命令。
- 每条测试结果。
- 未验证项、原因与风险。

测试记录建议使用 [test_report_template.md](./test_report_template.md)。
