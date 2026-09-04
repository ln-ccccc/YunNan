# 项目感知解译结果路由 Implementation Plan

> **For Codex:** REQUIRED SKILL: Use `executing-plans` to implement this plan task-by-task. Apply `test-driven-development` for every behavior change, `systematic-debugging` for any failing test/runtime deviation, and `verification-before-completion` before reporting success.

**Goal:** 修复 GeoView 地物分类请求的预检 401，并让从 Miner 当前项目进入的 TIFF 推理只匹配该项目的活动矿山矢量；命中时把分类、变化矩阵和光谱指数写回对应 FID，未命中或无项目上下文时只在 GeoView 展示。

**Architecture:** Miner 通过 `/segmentation?project_id=<id>` 显式传递当前项目。后端用一个共享路由服务解析项目活动矿山 GeoJSON/KML，并以 TIFF CRS、范围和有效像元判断命中的 FID；项目任务只处理这些 FID，独立模式复用现有 GeoView 地物分类展示。Worker 成功后以项目/FID/年份为键发布分类摘要、变化矩阵和数据集记录；光谱指数复用同一项目/FID 路由并写入项目 JSON。任何流程都不扫描其他项目，也不写新的全局 Miner 历史目录。

**Tech Stack:** Flask、SQLAlchemy、Rasterio、NumPy、Vue 3、Vite、Node `node:test`、Python `unittest`、Docker Compose。

---

## 执行约束与基线

- 当前工作区 `D:\项目\YunNan\.git` 为空，`git status` 返回“not a git repository”。执行计划时不得自行 `git init`；下列提交步骤仅在用户恢复 Git 元数据后执行，否则记录为“未执行：仓库元数据缺失”。
- 保留 `POST /api/inference/jobs` 的严格项目 API 语义，不把它改成隐式项目推断。兼容入口 `POST /api/analysis/kml_roi_inference` 负责 GeoView 的“项目/独立”分流。
- 独立模式不创建 KML ROI 任务：前端收到 `mode=standalone` 后复用现有 `/api/analysis/semantic_segmentation`，以保持当前 GeoView 历史记录和图片展示行为。
- 项目模式不使用 `miner/yunnan.kml`、`miner/change_matrix_outputs` 或全局指数 Excel 作为新结果写入目标。
- 每个任务遵循红—绿—重构：先写单个失败测试并运行确认，再做最小实现，再运行相关测试。

## Task 1：修复受保护接口的 CORS 预检

**Files:**

- Modify: `backend/test_auth_api.py`
- Modify: `backend/applications/auth/guard.py`

- [ ] **Step 1: 为 OPTIONS 放行、真实请求仍鉴权添加失败测试**

在 `TestAuthAPI` 中新增：

```python
def test_protected_preflight_bypasses_session_guard_but_post_does_not(self):
    headers = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }
    preflight = self.client.open(
        "/api/analysis/kml_roi_inference",
        method="OPTIONS",
        headers=headers,
    )
    self.assertEqual(preflight.status_code, 200)
    self.assertEqual(
        preflight.headers.get("Access-Control-Allow-Origin"),
        "http://localhost:3000",
    )

    actual = self.client.post("/api/analysis/kml_roi_inference", json={})
    self.assertEqual(actual.status_code, 401)
```

- [ ] **Step 2: 运行测试并确认当前失败点是 OPTIONS=401**

Run: `python -m unittest backend.test_auth_api.TestAuthAPI.test_protected_preflight_bypasses_session_guard_but_post_does_not`

Expected: FAIL，预检状态码为 401。

- [ ] **Step 3: 对所有使用 `ensure_logged_in` 的蓝图统一跳过 OPTIONS**

在 `backend/applications/auth/guard.py` 中只做方法级放行：

```python
from flask import jsonify, request, session


def ensure_logged_in():
    if request.method == "OPTIONS":
        return None
    if not session.get("admin_user_id"):
        return jsonify({"success": False, "code": 401, "msg": "未登录"}), 401
    return None
```

- [ ] **Step 4: 运行鉴权回归**

Run: `python -m unittest backend.test_auth_api`

Expected: PASS；OPTIONS 为 200，未登录 POST 仍为 401，未知 Origin 仍无 CORS 响应头。

- [ ] **Step 5: 记录检查点**

若 Git 元数据已恢复：

```powershell
git add backend/test_auth_api.py backend/applications/auth/guard.py
git commit -m "修复受保护接口的 CORS 预检"
```

否则在执行记录中注明未提交原因，不初始化仓库。

## Task 2：从 Miner 显式传递并在 GeoView 解析当前项目

**Files:**

- Modify: `miner/test/geoviewNavigation.test.js`
- Modify: `miner/src/navigation/geoviewNavigation.js`
- Modify: `miner/src/components/TheHeader.vue`
- Modify: `miner/src/components/MapDashboard.vue`
- Create: `frontend/test/interpretationContext.test.mjs`
- Create: `frontend/src/utils/interpretationContext.mjs`
- Modify: `frontend/src/utils/gosomewhere.js`
- Modify: `frontend/src/views/mainfun/Segmentation.vue`

- [ ] **Step 1: 先把 Miner 导航测试改成要求当前项目参数**

```javascript
test('buildGeoViewUrl passes only the active project id', () => {
  const result = buildGeoViewUrl(
    'http://localhost:3000/#/detectchanges?project_id=999',
    {
      href: 'http://192.168.1.20:4000/#/projects/7',
      hostname: '192.168.1.20',
    },
    7,
  );
  assert.equal(result, 'http://192.168.1.20:3000/segmentation?project_id=7');
});
```

另加无有效 ID 时不带查询参数的用例，防止沿用配置 URL 中的旧项目。

- [ ] **Step 2: 运行 Miner 单测确认失败**

Run: `node --test miner/test/geoviewNavigation.test.js`

Expected: FAIL，现有函数忽略第三个参数并清空查询参数。

- [ ] **Step 3: 最小修改 URL 生成器和组件传值**

`buildGeoViewUrl` 只接受正安全整数：

```javascript
export function buildGeoViewUrl(configuredUrl, currentLocation, projectId) {
  const target = new URL(configuredUrl, currentLocation.href);
  target.hostname = currentLocation.hostname;
  target.pathname = '/segmentation';
  target.search = '';
  target.hash = '';
  const value = Number(projectId);
  if (Number.isSafeInteger(value) && value > 0) {
    target.searchParams.set('project_id', String(value));
  }
  return target.toString();
}
```

在 `MapDashboard.vue` 给 `TheHeader` 传 `:projectId="projectId"`；在 `TheHeader.vue` 声明 `projectId` prop，并传给 `buildGeoViewUrl`。不要从路由文字或城市名猜项目。

- [ ] **Step 4: 为 GeoView 查询参数解析添加失败测试**

`frontend/test/interpretationContext.test.mjs`：

```javascript
import test from 'node:test';
import assert from 'node:assert/strict';
import { buildProjectRoute, readProjectId } from '../src/utils/interpretationContext.mjs';

test('readProjectId accepts only a positive safe integer', () => {
  assert.equal(readProjectId('?project_id=7'), 7);
  assert.equal(readProjectId('?project_id=0'), null);
  assert.equal(readProjectId('?project_id=dali'), null);
  assert.equal(readProjectId(''), null);
});

test('buildProjectRoute carries only a valid project id between tools', () => {
  assert.deepEqual(buildProjectRoute('/spectralindices', 7), {
    path: '/spectralindices',
    query: { project_id: '7' },
  });
  assert.deepEqual(buildProjectRoute('/segmentation', null), {
    path: '/segmentation',
    query: {},
  });
});
```

Run: `node --test frontend/test/interpretationContext.test.mjs`

Expected: FAIL，模块尚不存在。

- [ ] **Step 5: 创建纯解析函数并接入 Segmentation 状态**

```javascript
export function readProjectId(search = '') {
  const value = Number(new URLSearchParams(search).get('project_id'));
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

export function buildProjectRoute(path, projectId) {
  const value = Number(projectId);
  return {
    path,
    query: Number.isSafeInteger(value) && value > 0
      ? { project_id: String(value) }
      : {},
  };
}
```

在 `Segmentation.vue` 初始化 `projectId: readProjectId(window.location.search)`；不新增项目选择器，也不提供默认项目。修改 `gosomewhere.js`，从当前 route query 解析合法 `project_id`，并用 `buildProjectRoute` 跳转 `/segmentation` 或 `/spectralindices`，确保侧栏切换功能时项目上下文不会丢失，也不会携带其他任意查询参数。

- [ ] **Step 6: 运行前端导航测试和构建**

Run: `node --test miner/test/geoviewNavigation.test.js frontend/test/interpretationContext.test.mjs frontend/test/backendUrl.test.mjs`

Run: `npm --prefix miner run build`

Run: `npm --prefix frontend run build`

Expected: 所有测试及两个构建 PASS。

- [ ] **Step 7: 记录检查点**

若 Git 可用，提交消息：`显式传递当前解译项目上下文`。

## Task 3：统一读取当前项目矿山矢量并判断 TIFF 有效像元命中

**Files:**

- Modify: `backend/applications/kml_roi/kml.py`
- Modify: `backend/applications/kml_roi/pipeline.py`
- Create: `backend/applications/inference/routing.py`
- Create: `backend/test_inference_routing.py`
- Create: `backend/test_kml_roi_pipeline.py`

- [ ] **Step 1: 为 GeoJSON 矿山资源读取添加失败测试**

在 `backend/test_inference_routing.py` 用临时目录写一个 `FeatureCollection`，包含 `FID_1=101` 和一个 Polygon；断言通用加载器返回 `[("101", geometry)]`。再覆盖 `FID`、`OBJECTID`、feature `id` 的回退顺序，并断言没有 FID 的 feature 被忽略。

Run: `python -m unittest backend.test_inference_routing.TestVectorFeatures`

Expected: FAIL，`load_vector_features` 尚不存在。

- [ ] **Step 2: 保留 KML 解析并增加 GeoJSON 分支**

在 `kml.py` 新增：

```python
def load_vector_features(vector_path: Path) -> List[Tuple[str, Dict]]:
    suffix = vector_path.suffix.lower()
    if suffix == ".kml":
        return load_kml_features(vector_path)
    if suffix not in {".geojson", ".json"}:
        raise ValueError("项目矿山资源仅支持 KML/GeoJSON")
    payload = json.loads(vector_path.read_text(encoding="utf-8"))
    features = []
    for feature in payload.get("features") or []:
        properties = feature.get("properties") or {}
        fid = properties.get("FID_1", properties.get("FID", properties.get("OBJECTID", feature.get("id"))))
        geometry = feature.get("geometry")
        if fid not in (None, "") and geometry:
            features.append((str(fid), geometry))
    return features
```

把 `pipeline.py` 的 `load_kml_features` 调用换成通用加载器；其余瓦片和输出逻辑暂不动。

- [ ] **Step 3: 添加项目隔离与有效像元测试**

测试夹具创建：

- 项目 A，绑定 FID 101，活动 GeoJSON 覆盖测试栅格左半部；
- 项目 B，绑定 FID 202，活动 GeoJSON 与同一 TIFF 重叠；
- 一个 EPSG:4326 小 TIFF，左半部 mask 有效、右半部 nodata；
- 项目 A GeoJSON 再包含未绑定 FID 999。

断言：

```python
scope = resolve_interpretation_scope(project_a.id, tif_path)
self.assertEqual(scope["mode"], "project")
self.assertEqual(scope["matched_fids"], [101])
self.assertNotIn(202, scope["matched_fids"])
self.assertNotIn(999, scope["matched_fids"])
```

补充用例：无 `project_id` 返回 standalone；当前项目无相交有效像元返回 standalone；项目不存在/无活动资源抛出明确 `ValueError`；TIFF 无 CRS 返回 standalone + warning，绝不扫描项目 B。

Run: `python -m unittest backend.test_inference_routing.TestInterpretationRouting`

Expected: FAIL，路由服务尚不存在。

- [ ] **Step 4: 实现共享路由服务**

`routing.py` 的公共返回形状固定为：

```python
{
    "mode": "project" | "standalone",
    "project_id": int | None,
    "mine_resource_id": int | None,
    "vector_path": str | None,
    "matched_fids": list[int],
    "warnings": list[str],
}
```

实现顺序：

1. `project_id is None` 立即返回 standalone；
2. 只查询 `Project.id == project_id` 和该项目最新 active `mine_vector`；
3. 只保留 `project.mines` 已绑定的 FID；
4. 用 `rasterio.warp.transform_geom` 把矿山几何变换到 TIFF CRS；
5. 用 `geometry_window` 限制读取窗口，以 `read_masks(1, window=...) > 0` 与 `geometry_mask(..., invert=True)` 的交集判断实际有效像元；
6. 将命中 FID 排序去重；空集返回 standalone，但保留当前 project_id/resource_id 供提示；
7. 缺少 CRS 时不做项目同步，并添加“TIFF 缺少 CRS”警告。

不得读取项目名称、地区名或其他项目资源作兜底。

- [ ] **Step 5: 让 Pipeline 可接收明确 FID 白名单**

给 `run_kml_roi_pipeline(..., selected_fids=None)` 增加可选参数，在 bbox/切片前过滤 feature；无参数时保持现有调用兼容。Worker 后续只传路由已经确认的 FID。

在 `backend/test_kml_roi_pipeline.py` 增加两个相交 feature、`selected_fids=[101]` 时只处理 101 的测试。

- [ ] **Step 6: 运行空间路由及 Pipeline 回归**

Run: `python -m unittest backend.test_inference_routing backend.test_kml_roi_pipeline`

Expected: PASS。

- [ ] **Step 7: 记录检查点**

若 Git 可用，提交消息：`按当前项目和有效像元匹配矿山`。

## Task 4：GeoView 按路由结果创建项目任务或走独立展示

**Files:**

- Create: `backend/applications/inference/interpretation.py`
- Modify: `backend/applications/api/analysis.py`
- Modify: `backend/applications/inference/worker.py`
- Modify: `backend/applications/inference/jobs.py`
- Create: `backend/test_interpretation_api.py`
- Modify: `backend/test_inference_runner.py`
- Modify: `frontend/src/utils/getUploadImg.js`
- Modify: `frontend/src/api/upload.js` only if the existing wrapper cannot carry the new optional field unchanged

- [ ] **Step 1: 为兼容入口添加分流 API 测试**

使用 Flask 测试客户端登录后，mock `resolve_interpretation_scope` 和 `create_job`：

- 不传项目：200，`data.mode == "standalone"`，无 job id，不调用 `create_job`；
- 项目未命中：同样 standalone，返回 warnings 和空 `matched_fids`；
- 项目命中 `[101, 102]`：201，返回 `mode=project`、job 序列化数据和两个 FID；
- 项目不存在/无活动矿山资源：400，消息说明项目配置问题，不降级为其他项目或全局 KML；
- 上传路径不在 `UPLOADED_PHOTOS_DEST`：400。

Run: `python -m unittest backend.test_interpretation_api`

Expected: FAIL，兼容入口仍直接调用严格项目 API。

- [ ] **Step 2: 实现 GeoView 专用任务编排，不改变严格项目 API**

`interpretation.py` 实现：

```python
def prepare_geoview_interpretation(payload):
    tif_path = resolve_uploaded_tiff(payload.get("new_tif_path"))
    scope = resolve_interpretation_scope(parse_optional_project_id(payload), tif_path)
    if scope["mode"] == "standalone":
        return {**scope, "job": None}

    project_input = publish_project_input_atomically(
        scope["project_id"], tif_path, payload.get("year")
    )
    normalized = normalize_job_request(
        {
            **payload,
            "project_id": scope["project_id"],
            "old_tif_path": str(project_input),
            "new_tif_path": str(project_input),
            "kml_path": scope["vector_path"],
            "output_root": str(project_output_root),
            "mine_fids": scope["matched_fids"],
        },
        allowed_roots=[project_root],
        allowed_output_roots=[project_root / "outputs"],
    )
    return {**scope, "job": create_job(normalized)}
```

关键约束：

- `resolve_uploaded_tiff` 必须用 `Path.resolve()` + `relative_to(upload_root)` 防路径穿越；
- 复制到 `projects/{project_id}/inputs/interpretation/{year}/` 时先写临时文件，再 `replace`；
- 年份必须是可转换的四位整数；
- 路由失败时不留下项目输入副本；
- 兼容入口负责构造 HTTP 响应，业务函数不依赖 request 全局。

- [ ] **Step 3: 让 Worker 只处理已匹配 FID，并把项目结果先写任务暂存目录**

`normalize_job_request` 保留并规范化 `mine_fids` 为正整数列表。项目任务不能让 Pipeline 直接覆盖正式输出：`InferenceWorker._run_pipeline` 对带 `project_id` 的任务使用 `work_dir / "staged_outputs"` 作为实际 `output_root`，payload 中的项目 output root 只作为最终发布目标和路径校验依据；非项目任务保持原行为。传入：

```python
selected_fids=payload.get("mine_fids") or None
```

在 `backend/test_inference_runner.py` 断言 pipeline runner 收到项目路由给定的 FID、不扩展到其他矿山，并且 runner 的 output root 位于当前 job work directory，而不是正式项目目录。

- [ ] **Step 4: 前端添加项目 ID，并对 standalone 复用现有分类流程**

在 `getUploadImg.js` 每个 TIFF 请求中加入：

```javascript
const response = await kmlRoiInfer({
  old_tif_path: tifPath,
  new_tif_path: tifPath,
  year: roiYear,
  device: 'auto',
  ...(this.projectId ? { project_id: this.projectId } : {}),
});
const routed = response?.data?.data || {};
if (routed.mode === 'standalone') {
  await this.imgUpload(this.uploadSrc, 'semantic_segmentation');
  standaloneHandled = true;
  continue;
}
const createdJob = routed.job || routed;
```

上传响应中的每个切片都带 `raw_tiff_path`。先按该字段建立 `Map<rawTiffPath, src[]>`，对 standalone 路由只收集对应 TIFF 的切片，最后用 `{ ...this.uploadSrc, list: standaloneSrc }` 调用一次 `imgUpload`。这样同一批中“一个命中、一个未命中”时，不会把已命中的 TIFF 再次按独立模式提交。任务结束提示区分：

- `未匹配当前项目矿山，结果仅在解译平台展示`；
- `已同步到当前项目 N 个矿山`。

不要把 standalone 结果写入项目，也不要用缺失 project_id 当作 1 号项目。

- [ ] **Step 5: 运行 API、Worker 和前端构建**

Run: `python -m unittest backend.test_interpretation_api backend.test_inference_runner backend.test_inference_jobs`

Run: `npm --prefix frontend run build`

Expected: PASS。

- [ ] **Step 6: 记录检查点**

若 Git 可用，提交消息：`分流项目推理与独立解译展示`。

## Task 5：发布项目分类、最近前期变化矩阵与弹窗图片

**Files:**

- Create: `backend/applications/project_hub/inference_results.py`
- Create: `backend/test_project_inference_results.py`
- Modify: `backend/applications/inference/worker.py`
- Modify: `backend/applications/api/project.py`
- Modify: `backend/test_project_map.py`
- Modify: `miner/routes/projects.js`
- Modify: `miner/test/projectRoutes.test.js`
- Modify: `frontend/src/utils/getUploadImg.js`
- Modify: `miner/src/components/MineDetailModal.vue`

- [ ] **Step 1: 为项目发布规则添加失败测试**

`backend/test_project_inference_results.py` 创建项目/FID 绑定和临时推理输出：

- 只有 `101+2022.png`/mask：写 `change_matrix/101.json`，`has_change_matrix=false`，`new_year=2022`，地物分类仍有 new 图片；
- 已有 2018、2020，再发布 2022：基线必须是 2020；
- 已有 2020、2022，再补录 2018：弹窗最新对仍是 2020→2022；
- 同一 FID/年份重跑：`ProjectDataset(dataset_kind="inference_result")` upsert，不新增重复行；
- 发布 FID 不属于项目：拒绝；
- 项目 1 的发布文件不会出现在项目 2 的输出或数据集。

Run: `python -m unittest backend.test_project_inference_results`

Expected: FAIL，发布服务尚不存在。

- [ ] **Step 2: 实现原子发布和摘要 JSON**

公共入口：

```python
def publish_project_inference_result(project_id, request_payload, pipeline_summary):
    # validate project and written_fid_list
    # upsert one ProjectDataset per project/fid/year
    # scan all successful year masks for each fid
    # select the latest two years, never the two request fields blindly
    # write outputs/change_matrix/{fid}.json atomically
    # return display_results and synced_fids
```

摘要保持 Miner 现有读取形状，并新增真实年份：

```json
{
  "fid": 101,
  "old_year": 2020,
  "new_year": 2022,
  "headers": ["grassland", "forest"],
  "matrix": [
    {"label": "grassland", "values": [90.0, 10.0]},
    {"label": "forest", "values": [5.0, 95.0]}
  ],
  "images": {
    "old": "/api/projects/1/outputs/inference/101/101+2020.png",
    "new": "/api/projects/1/outputs/inference/101/101+2022.png"
  },
  "has_change_matrix": true,
  "data_source": "project_inference"
}
```

只有一个年份时 `old_year=null`、`images.old=null`、`has_change_matrix=false`，但 `images.new` 必须可用。

发布必须从 Worker 的 `staged_outputs` 复制到正式目录：先把所有待发布文件复制为正式目录内的临时文件；再把同名旧文件改名为备份并原子替换；随后提交数据库。任一步失败都 rollback 数据库、删除新文件并恢复备份，确保同年重跑失败时原成功结果仍可读取。成功后删除备份。`ProjectDataset` 的 upsert 键明确为 `(project_id, dataset_kind="inference_result", mine_fid, year_start, year_end)`。

- [ ] **Step 3: Worker 仅在项目任务成功后调用发布服务**

在 `finish_job` 前调用发布服务，并把：

```python
summary["routing"] = {
    "mode": "project",
    "project_id": project_id,
    "matched_fids": payload["mine_fids"],
    "synced_fids": publication["synced_fids"],
}
summary["display_results"] = publication["display_results"]
```

写入 job result。发布失败应把任务置为 failed 并保留旧成功摘要，不得报告“推理成功但实际上未同步”。

- [ ] **Step 4: 增加受保护且路径安全的项目结果文件接口**

Flask 添加：

```text
GET /api/projects/<project_id>/outputs/inference/<fid>/<filename>
```

要求项目存在、FID 绑定当前项目、filename 只允许当前 FID 的 `.png/.json/.csv` 结果，使用 `resolve_storage_path` 防穿越。`backend/test_project_map.py` 覆盖正常图片、跨项目 FID、`../` 和未登录。

Miner 的 `/api/projects` Express 路由增加同路径的二进制文件读取，使用挂载的 `PROJECT_STORAGE_ROOT`、同样的数字/path basename 校验和既有 `authGuard`；不要用 JSON relay 读取图片。`miner/test/projectRoutes.test.js` 覆盖 200、404 和穿越拒绝。

- [ ] **Step 5: GeoView 使用 job 返回的结果 URL**

删除 `getUploadImg.js` 对全局 `/api/analysis/kml_roi_output/{fid}/...` 的新任务 URL 拼接；改为遍历 `job.result.display_results`，把其中相对 backend URL 用现有 `global.BASEURL` 规范化。旧历史读取接口不删除。

- [ ] **Step 6: 弹窗使用真实年份，并允许单年分类图片**

`MineDetailModal.vue`：

- 变化矩阵角标显示 `old_year/new_year`；
- 分类页展示条件改为“任意 images.old/new 存在”，不要求 `has_change_matrix`；
- 标题和图片年份从 JSON 读取；
- 无前期结果时只展示当前年份图片，并显示“暂无可比较的前期分类结果”。

- [ ] **Step 7: 运行发布、路由和构建回归**

Run: `python -m unittest backend.test_project_inference_results backend.test_project_map backend.test_inference_runner`

Run: `node --test miner/test/projectRoutes.test.js`

Run: `npm --prefix miner run build`

Run: `npm --prefix frontend run build`

Expected: PASS。

- [ ] **Step 8: 记录检查点**

若 Git 可用，提交消息：`发布项目分类与真实年份变化矩阵`。

## Task 6：光谱指数复用同一项目/FID 路由

**Files:**

- Modify: `backend/applications/interface/analysis.py`
- Modify: `backend/applications/api/analysis.py`
- Modify: `backend/applications/project_hub/inference_results.py`
- Modify: `backend/test_spectral_indices.py`
- Modify: `backend/test_project_inference_results.py`
- Modify: `frontend/src/views/mainfun/SpectralIndices.vue`

- [ ] **Step 1: 为独立模式和项目模式添加失败测试**

扩展 `backend/test_spectral_indices.py`：

- 无 project_id：完成指数计算，只返回整图统计，不加载默认 `miner/yunnan.kml`，不调用全局 `sync_miner_index_rows`；
- project_id=项目 A：只读取项目 A 的 active mine vector，只统计且写入命中的绑定 FID；
- 项目 A 未命中但项目 B 命中：不写任何项目文件；
- TIFF 缺 CRS：仍返回 GeoView 指数图和 warning，不写项目；
- 同一 FID/指数/年份重跑更新该点，不重复；
- 追加年份后 `mean`、线性趋势和 `mk_trend` 重新计算，JSON 仍兼容 `useMineData.js`。

Run: `python -m unittest backend.test_spectral_indices backend.test_project_inference_results`

Expected: FAIL，现有实现仍会默认全局 KML/Excel 同步。

- [ ] **Step 2: 让光谱计算显式接收项目上下文**

API 从请求读取可选 `project_id`，对原始 TIFF 调用 Task 3 的 `resolve_interpretation_scope`。调用核心计算时传明确参数：

```python
spectral_index_calculation(
    up_dir,
    generate_dir,
    img_list,
    index_type,
    year,
    normalized_band_map,
    type_=8,
    vector_path=scope.get("vector_path"),
    allowed_fids=scope.get("matched_fids") or [],
    sync_global=False,
)
```

核心函数行为：

- `vector_path is None` 时不再隐式打开默认 KML；
- 有 vector_path 时使用 `load_vector_features`；
- 只返回 allowed_fids 的 polygon stats；
- 保留现有生成指数图和 Analysis 历史记录行为。

- [ ] **Step 3: 以项目 JSON 格式 upsert 指数点**

在 `inference_results.py` 增加：

```python
def upsert_project_index_results(project_id, index_type, year, fid_stats):
    # validate bound fids
    # load or initialize outputs/indices/{fid}.json
    # replace same year point, sort by year
    # recalculate mean/trend/mk_trend/available
    # atomic write
```

保持现有形状：

```json
{
  "fid": 101,
  "ndvi": {
    "data": [{"year": 2022, "value": 0.18}],
    "mean": 0.18,
    "trend": 0.0,
    "mk_trend": "stable",
    "available": true,
    "source_file": "project_inference",
    "reason": null,
    "message": ""
  }
}
```

其他指数键保留原值；未计算过的键由 Miner 现有空数据逻辑处理。新项目结果不写全局 Excel。

- [ ] **Step 4: 前端光谱请求携带同一个 project_id**

定位实际光谱请求组件，把 `Segmentation` 同样的 `readProjectId(window.location.search)` 接入 payload；无有效 ID 时不发送字段。成功提示区分“仅解译平台展示”和“已同步 N 个矿山”。

- [ ] **Step 5: 运行光谱回归和前端构建**

Run: `python -m unittest backend.test_spectral_indices backend.test_project_inference_results backend.test_project_map`

Run: `npm --prefix frontend run build`

Expected: PASS。

- [ ] **Step 6: 记录检查点**

若 Git 可用，提交消息：`按当前项目同步光谱指数`。

## Task 7：端到端回归、运行验证与文档同步

**Files:**

- Modify: `docs/system_guide.md`
- Create: `docs/verification/2026-08-20-project-aware-interpretation-routing.md`

- [ ] **Step 1: 运行后端相关完整测试集**

Run:

```powershell
python -m unittest `
  backend.test_auth_api `
  backend.test_inference_jobs `
  backend.test_inference_runner `
  backend.test_inference_routing `
  backend.test_interpretation_api `
  backend.test_kml_roi_pipeline `
  backend.test_project_inference_results `
  backend.test_project_map `
  backend.test_spectral_indices
```

Expected: PASS。若宿主环境缺少后端 Python 依赖，使用已启动 backend 容器执行等价命令，并在验证记录中写明实际命令和镜像。

- [ ] **Step 2: 运行 Miner/GeoView 单测和生产构建**

Run: `npm --prefix miner test`

Run: `node --test frontend/test/*.test.mjs`

Run: `npm --prefix miner run build`

Run: `npm --prefix frontend run build`

Expected: PASS；如果 PowerShell 不展开 `*.test.mjs`，用 `Get-ChildItem frontend/test/*.test.mjs | ForEach-Object { node --test $_.FullName }`。

- [ ] **Step 3: 重建并检查服务健康状态**

Run: `docker compose -f docker-compose.gpu.yml ps`

按实际 compose 服务名只重建受影响的 backend、worker、frontend、miner 服务；不要删除数据库卷或项目存储。重建后再次运行 `docker compose ... ps`，Expected: 相关服务 Up/healthy、无 restart loop。

- [ ] **Step 4: 用真实浏览器验证四条关键路径**

1. 登录 Miner，打开大理项目并进入 GeoView，确认地址包含唯一的 `project_id`；
2. 上传命中大理当前项目矿山的带 CRS TIFF：预检 200、任务创建成功、GeoView 显示分类，任务结果 `synced_fids` 与当前项目绑定一致；
3. 上传不命中当前项目的 TIFF：GeoView 显示独立分类，当前项目 `outputs` 和 `ProjectDataset` 无新增；
4. 切换昆明项目再进入 GeoView：只读取昆明 active mine resource，重复相同 TIFF 时不能把结果写进大理；
5. 对同一 FID 依次上传两个年份：弹窗地物分类显示真实年份，变化矩阵使用最近前期年份；补传更老年份后最新比较对不倒退；
6. 运行一个光谱指数：命中时只更新当前项目命中 FID 的指数序列，未命中时只在 GeoView 显示。

浏览器网络面板需确认：OPTIONS 200，实际 POST 带登录 cookie；不能只根据 toast 判断。

- [ ] **Step 5: 检查项目间文件和数据库隔离**

对验证产生的 job id、项目 id 和 FID，检查：

- 输入只在 `project_storage/projects/{current}/inputs/interpretation/{year}`；
- 分类只在 `project_storage/projects/{current}/outputs/inference/{fid}`；
- 摘要只在当前项目的 `outputs/change_matrix` / `outputs/indices`；
- `ProjectDataset.project_id/mine_fid/year_*` 与当前项目一致；
- `miner/change_matrix_outputs` 和全局指数 Excel 未出现本次新写入。

- [ ] **Step 6: 同步文档和验证记录**

文档至少说明：

- Miner 传 `project_id` 的 URL 契约；
- 无 project_id/无命中时的 standalone 行为；
- 当前项目 active mine vector 是唯一匹配来源；
- 项目分类、变化矩阵、指数输出目录；
- 真实鉴权仍作用于 POST；
- 验证命令、实际结果、任何既有失败和未验证风险。

- [ ] **Step 7: 最终差异审查**

Run: `git diff --check`（仅 Git 元数据已恢复时）

逐文件确认没有依赖升级、批量格式化、全局历史迁移或跨项目扫描。使用 `rg` 确认新流程不再调用 `default_kml_path`、`miner_change_output_root`、`sync_miner_index_rows`。

- [ ] **Step 8: 最终检查点**

若 Git 可用，提交消息：`完成项目感知解译结果路由`。若不可用，在交付中明确列出未提交限制和所有修改文件。

## 完成标准

- 原截图场景不再因 OPTIONS 401 显示“网络异常”；未登录真实请求仍是 401。
- 从 Miner 当前项目进入时，后端只使用该项目当前 active KML/GeoJSON 和该项目绑定 FID。
- 命中一个或多个矿山时，GeoView 显示结果，并只写入这些 FID 的项目分类/变化矩阵/指数。
- 无项目上下文、未命中或 TIFF 无 CRS 时，结果只留在 GeoView；不会猜项目或写 Miner 全局目录。
- 变化矩阵使用同一项目同一 FID 的最近前期成功年份；单年也能在“地物分类”页显示。
- 大理/昆明切换和跨项目隔离有自动化测试与真实运行证据。
