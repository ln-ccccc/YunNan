# 真实 case 端到端测试记录：保存竞态与 ID 追踪（2026-10-31）

对应修复：`9957cf6` 保存竞态两处遗留（正式图斑 ID 同步 + 保存提交边界不可合并）及其前序 `49706d1`/`7854dc0`/`8757b85`。本文记录用**真实栈 + 真实数据**对上述修复做的一次完整端到端验证，含测试方法、全部过程截图与数据库核对。

**补充轮（同日第二轮）**：应用户要求补上"调用地物分类推理"环节——从 Miner 智能解译页真实发起推理，走完「推理 → worker 自动发布 → 自动矢量化生成新成果 → 对新成果竞态编辑」全链，见第 7 节。

## 1. 环境与方法

| 项 | 值 |
| --- | --- |
| 被测栈 | 本机 Docker 运行栈：`yunnan-frontend:3000`（GeoView）、`yunnan-backend:5008`（Flask/gunicorn）、`yunnan-mysql`（库 `paddle_rs`）；容器挂载主树 `D:\项目\YunNan`（main，含全部修复提交） |
| 生效方式 | 测试前 `docker restart yunnan-backend yunnan-frontend`（gunicorn 内存模块与前端现场构建均需重启加载修复代码） |
| 测试数据 | 项目 10 `real-data-retest-2026-10-27`（复测专用项目，active）· 成果 111（fid 11195/2024）：基线 v0 auto(130 面) → v1 manual(130) → v2 manual(130)，全部 `auto-111-*` ID |
| 驱动方式 | Playwright 真实 Chromium（headed），真实登录页 admin 登录（密码取根 `.env` 的 `ADMIN_PASSWORD`，不落日志）、真实鼠标点选/改类/绘制、真实后端处理全部请求 |
| **唯一干预** | 对 `POST /api/projects/10/classification-results/111/revisions` 做 **3.5 秒透明延迟**（`route.continue()` 原样透传请求与响应）——真实后端响应为毫秒级，人工无法命中"保存期间继续编辑"的竞态窗口；除延迟外**不 mock 任何内容** |
| 脚本与产物 | 脚本 `D:/Temp/yunnan-fix-20261031/real-case/real-case-race.cjs`；截图 `…/real-case/screenshots/S01…S13.png`；结果 `…/real-case-result.json` |

## 2. 修复前置确认（真实栈）

重启后先验证后端错误分型修复已生效（curl 直打真实后端）：

| 请求 | 修复前 | 实测 |
| --- | --- | --- |
| POST `/api/projects/10/exports` 空 body（JSON 头） | 500 | **400** |
| 同上，body=`{` | 500 | **400** |
| 同上，`Content-Type: text/plain` | 500 | **415** |
| 同上，body=`null` | 500 | **422** |

## 3. 测试过程（每步截图）

| 步骤 | 截图 | 过程与断言 |
| --- | --- | --- |
| S1 | `S01-login-page.png` `S02-after-login.png` | 打开 :3000 真实登录页，admin 真实登录（真实 session cookie） |
| S2 | `S03-editor-loaded.png` | 打开成果 111 编辑器：工具栏"当前版本已保存"、版本记录 V0-V2(130 面)、真实项目底图瓦片渲染 |
| S3 | `S04-select-feature.png` | 真实鼠标点选既有图斑成功（WebGL 拾取，命中点 920,657） |
| S4 | `S05-change-class.png` | 改类为林地，出现"存在未保存编辑" |
| S5 | `S06-draw-new-feature.png` | 在命中点附近绘制新面 1（约 1.6m 微型面，提交时以 `client-*` 请求正式 ID） |
| S6 | `S07-save-pending.png` | 点保存 → 请求进入 3.5s 延迟窗口；**截图实锤：保存按钮 loading、六个改类按钮全部禁用**（保存冻结） |
| S7 | `S08-edit-during-save.png` | 挂起期间通过绘制控件真实绘制第二个新面——编辑未被拒绝（绘制控件不冻结，由代次+快照双比对兜底） |
| S8 | `S09-race-response-warning.png` | 延迟放行、真实响应返回：**warning"已保存为 V4；保存期间又有新的修改，请再次保存。"**；画布未被覆盖、V4(132 面)落库、撤销/重做按钮可用 |
| S9 | `S10-undo-works.png` `S11-redo-restore.png` | **撤销可用并成功回退一步**（`markSubmitBoundary` 提交边界生效，未清栈）；重做恢复 |
| S10 | `S12-second-save-ok.png` | 再次保存：**成功"已保存为 V5"（133 面）**，工具栏回到"当前版本已保存"；版本记录呈现完整链 V0→V5 |
| S11 | `S13-zoom-in-6x.png` | 真实底图连续放大 6 次：请求瓦片最高 **z15**（真实 manifest 声明的 max_zoom），**越级 404=0**、无失败请求 |

首轮排障记录（无数据写入）：第一版脚本用画布中心偏移 ±60px 画面，被服务端 422 拒绝（fid 11195 的 ROI 仅约 150m×90m，`roi.covers` 校验正确拒绝越界面）——据此把新面改为"点选命中点附近微型面"后一次通过。422 为预期校验行为，未产生脏数据。

## 4. 数据库核对（paddle_rs.classification_revisions）

测试后导出 v3/v4/v5 修订的 `feature_collection_json` 逐版对比 `feature_id`：

| 修订 | 面数 | ID 构成 | 相对上一版 |
| --- | --- | --- | --- |
| v3（第一轮竞态保存） | 131 | 130×`auto-111-*` + 1×`manual-111-*` | 新面获正式 ID |
| v4（本轮竞态保存） | 132 | 130×`auto-111-*` + 2×`manual-111-*` | 新增 `manual-111-ec12ed2f…`（新面 1）；**v3 的 ID 零丢失** |
| v5（再次保存） | 133 | 130×`auto-111-*` + 3×`manual-111-*` | 新增 `manual-111-668f6fd7…`（新面 2）；**v4 的 ID 零丢失** |

- **`client-*` 残留：0**——竞态分支同步正式 ID 后，再次保存提交的全部是服务端正式 ID，跨版本图斑追踪无断裂。
- 导出文件：`D:/Temp/yunnan-fix-20261031/real-case/rev{3,4,5}-export.json`。

## 5. 结论

真实栈、真实账号、真实数据下完整复现"保存未返回时继续编辑"竞态：修改不丢失（画布保留+warning）、撤销/重做可用、正式图斑 ID 跨版本稳定、底图缩放不越级——三项修复（P1 竞态、P2 ID 同步/提交边界、P2 zoom）与后端错误分型在真实环境全部生效。

## 6. 遗留与说明

- 本轮在成果 111 上留下 v3/v4/v5 三版真实修订（不可变追加设计，无删除接口）；项目 10 为复测专用项目，与 2026-10-27 复测留痕同类。如需清理走项目删除/快照流程，另需负责人确认。
- 透明延迟代理仅作用于保存 POST 的传输时机，请求/响应内容与真实后端交互完全一致；这属于时序控制手段而非 mock，结论代表真实链路行为。
- 截图与导出 JSON 位于 `D:/Temp/yunnan-fix-20261031/real-case/`（仓库外，不入库）。

## 7. 补充轮：从发起推理开始的完整闭环（同日第二轮）

第一轮测试从既有成果（10-27 复测留存的 result 111）起步，未覆盖推理环节。本轮补齐：**真实发起地物分类推理 → worker 自动发布 → 自动矢量化生成新成果 → 对新成果执行同一套竞态编辑测试**。

### 7.1 方法

| 项 | 值 |
| --- | --- |
| 入口 | Miner 智能解译页（:4000 `#/interpretation`）→「发起新解译」→「项目地物分类」弹窗 |
| 推理影像 | 「大理2024真实影像-复测1027(573M)」（dataset 74，10-27 复测上传的真实影像；项目内共 3 个就绪影像资产） |
| 设备 | 自动（优先 NVIDIA GPU）；推理范围由影像范围 ∩ 项目激活矿山矢量自动求交（**8 个矿山**，无手动 FID 选择） |
| 脚本 | `D:/Temp/yunnan-fix-20261031/real-case/real-case-inference.cjs`；除保存 POST 透明延迟外零 mock |
| job | `d3dae669-b199-4cd0-9300-de82f5c74735` |

发布机制说明：推理 succeeded 后由 **inference worker 自动调用** `publish_project_inference_result`（`backend/applications/inference/worker.py:460-480`），PNG 产物落盘、每个写入 FID 在 `classification_results` 插行并**同步自动矢量化**（revision 0 = auto，`vector_status=ready`）——全链无人工发布步骤。

### 7.2 过程截图（R01-R17）

| 步骤 | 截图 | 内容 |
| --- | --- | --- |
| R1 | `R01/R02` | Miner 真实登录 → 项目工作台 |
| R2 | `R03-R06` | 智能解译页选项目 → 推理弹窗（影像下拉=3 个就绪资产）→ 选影像 → 「确认开始推理」提交 |
| R3 | `R07/R08/R09` | 轮询真实状态 queued → running → **succeeded 8/8**（8 矿山全部推理成功）；历史刷新出 8 条记录（每矿山原图+分类预测 PNG） |
| R4 | — | API 确认新成果：fid 11195 → **result_id=119，vector_status=ready，V0=130 面**（自动矢量化成功） |
| R5 | `R10-R12` | 新成果 119 编辑器：点选/改类/绘制新面 1（client-*） |
| R6 | `R13-R15` | 保存透明延迟 → 挂起期间绘制新面 2 → 放行：**warning"已保存为 V1；保存期间又有新的修改，请再次保存。"**、画布保留 |
| R7 | `R16/R17` | 撤销+重做可用（提交边界）；再次保存 **成功为 V2（132 面）** |

关键帧：`R08-inference-done.png`（8 矿山推理产物）、`R15-race-warning.png`（**V0 自动基线 130 面 · 2026/10/31 17:35:11 = 本轮推理产生**，V1 竞态保存 131 面）。

### 7.3 数据库核对（result 119，导出 `rev119-{0,1,2}-export.json`）

| 修订 | 面数 | ID 构成 | 相对上一版 |
| --- | --- | --- | --- |
| v0（本轮推理自动矢量化） | 130 | 130×`auto-119-*` | 推理产物 ID 命名空间全新（`auto-119-*`，与 result 111 的 `auto-111-*` 完全隔离） |
| v1（竞态保存） | 131 | 130 auto + 1 manual | 新增 `manual-119-ca386ddc…`（新面 1）；v0 ID 零丢失 |
| v2（再次保存） | 132 | 130 auto + 2 manual | 新增 `manual-119-b65859e9…`（新面 2）；v1 ID 零丢失、`client-*` 残留 0 |

### 7.4 补充轮结论

从「用户点击确认开始推理」到「竞态编辑后正式 ID 跨版本稳定」全链真实环境闭环：真实 GPU/推理容器处理 8 矿山影像、worker 自动发布与矢量化产出全新成果（130 图斑）、编辑器对该成果的竞态保护与 ID 同步全部生效。
