# 多方法测试报告：OCR×agent-skills 审查 + GUI 全功能实操（2026-10-01）

按用户指令执行三套方法的测试：①open-code-review（OCR 委托模式）②agent-skills 五轴
③本仓自有方法论（testing-playbook GUI 黑盒 + 真实栈）。**GUI 实操为最重要一环**。

## 一、功能完成度结论

范围内功能全部完成：甲方《优化修改建议》矩阵内可做项全落地（智能复核/编辑辅助/DXF/
推理审计/底图扩格式/时间口径/台账北京时间/结构重构 H3/M2/M3）；AGENTS §9 明确排除项
（在线训练/RBAC/React 重写）不做；L1-L7 低风险项随日常迭代。

## 二、GUI 全功能实操（真实栈·Playwright 真实浏览器·零污染）

### miner 平台八模块（15 项断言，14 直接过 + 1 项复验通过）

| 模块 | 结果 | 证据 |
| --- | --- | --- |
| 登录（错误密码红色提示/正确登录） | ✅ | "账号或密码错误"+进工作台 |
| 项目工作台（列表/统计带/详情八锚点/活动） | ✅ | 锚点 8 个，截图 02 |
| 图斑清单时间口径 | ✅ | 本地显示（北京时间） |
| 智能解译（记录排序/智能复核弹窗/阈值重筛） | ✅ | 7 记录 6 复核入口；指标卡 3/566/17/2,684.82；阈值 3000 重筛疑似清单 2 表，截图 03 |
| 数据管理（DXF 导出→制品下载） | ✅ | 导出记录+artifact.dxf 下载事件，截图 04 |
| 影像管理（清单 49 条→选中→裁剪/外扩表单） | ✅ | 完整用户路径复验（选影像后表单渲染：hasBuffer/bufferInput/clipBtn 全 true），截图 05 |
| 查询搜索（713 跨项目定位） | ✅ | 命中并定位地图，截图 06 |
| 系统设置/登出 | ✅ | 页面正常/回登录页 |

初测 13/15：两处"失败"经定位均为**脚本断言条件不足**而非缺陷——影像管理表单需先选影像
（v-if=selectedCandidate）；搜索 placeholder 匹配问题。按真实用户路径复验全过。

### GeoView 编辑器（P0 修复后全链 + 高亮像素级验证）

- 双选合并/锯齿/撤销/重载全链（此前已验）+ **本轮新增：选中橙色高亮像素级验证**——
  修复 `active` 取键后面内像素 R122/B43（暖色叠加）vs 空白区冷色，选中视觉反馈首次真正生效。

## 三、OCR×agent-skills 双 agent 审查（上下文隔离）

- 规则来源：`ocr delegate rule`（快照 docs/code-review/ocr-system-rules-snapshot.md）+ AGENTS 契约 + 五轴。
- 后端 agent：13 文件完整通读 + AST 级拆分等价性比对（47 函数逐字节等价、re-export 26 处全可导入）。
- 前端 agent：11 文件完整通读 + 依赖源码级验证（mapbox-gl-draw/turf/maplibre style-spec）+ 运行时实测。

### Findings 与处置（全部修复，两提交）

**后端**：
- **P1（严重）**：H3d 的 unused-imports 清理误删 `delete_project` 仍在用的 `Path`/`datetime`——
  归档项目删除/批量删除必现 NameError。三层证据（symtable 静态证明/git 取证/影响面）。
  **教训**：清理后仅跑 62 例（不含 management 的 delete 用例）即提交，全量声明是清理前数据——
  已沉淀规则：清理 imports 后必须 pyflakes(undefined) + 受影响域全量。修复+329 例回归。
- P2：DXF class_code 客户端可控值无校验，'水田'等外泄解释器原文（违反"异常原文不外泄"）。
  白名单校验+三态测试（含 fail_api HTTP 200+success=False 语义的断言修正）。
- P3×2：四模块死导入清理（re-export 兼容块保留）+ 台账北京时间值级断言。

**前端**：
- **P1-1**：上轮我把 drawStyles 的 `active`/"'true'" 误判为缺陷改成 `user_active`+布尔——
  agent 用 mapbox-gl-draw 源码证明 active 本就是字符串、原写法正确，我的"修复"反而引入真缺陷
  （选中恒不高亮）。**回滚官方写法+GUI 像素级验证**。
- P1-2：QualityReviewModal 竞态（seq 门控缺失）+ 阈值前置校验。
- P2×3：simplify 坐标级比较（键序恒不等缺陷）；merge/simplify 先 add 后 delete 原子化+逐面保护；
  loadEditorTargets 竞态门控。
- P3×2：suppressDrawEvents 防御注释；死注释清理。

## 四、验证基线（Docker 故障前完成）

- 后端**真全量 1598 passed / 40 skipped**；miner 91/91；GeoView 56/56；双前端构建绿。
- GUI 实操全部在故障前完成（含截图 6 张存 docs/images/gui-walkthrough-20261001/）。

## 五、遗留（Docker Desktop 深度僵死）

收尾重启容器时 Docker Desktop 引擎故障（WSL 0x80080005 COM 错，`wsl --shutdown`/
重启 LxssManager/重启 Desktop 均无效，诊断日志仅泛化 engine error）——**需重启 Windows
整机恢复**（未擅自执行）。影响：①容器栈暂不可用 ②miner-web 容器需重启一次使最新前端
修复进入其 dist（本地构建已绿）。后端/miner-api 为活挂载，恢复后 restart 即生效。
恢复后建议：`docker start` 全部容器 + GUI 快速冒烟一轮。
