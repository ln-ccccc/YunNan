# 真实 case 端到端测试记录：保存竞态与 ID 追踪（2026-10-31）

对应修复：`9957cf6` 保存竞态两处遗留（正式图斑 ID 同步 + 保存提交边界不可合并）及其前序 `49706d1`/`7854dc0`/`8757b85`。本文记录用**真实栈 + 真实数据**对上述修复做的一次完整端到端验证，含测试方法、全部过程截图与数据库核对。

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
