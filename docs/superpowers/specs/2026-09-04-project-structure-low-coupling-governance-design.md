# 项目结构与低耦合治理设计

> 设计状态：已获用户确认，待用户审阅本文档后再进入代码实施计划。
> 权威运行规则：[根目录 AGENTS.md](../../../AGENTS.md)。
> 详细领域契约：[项目结构与低耦合契约 v1](../../architecture/project-structure-and-low-coupling-contract-v1.md)。

## 背景

项目已经形成 Flask、Miner BFF、Miner Web、GeoView、空间 Worker、推理 Worker 与离线部署的多模块系统。当前项目工作台把多个职责集中在单一页面中，且项目状态、资源状态、任务状态和项目准备情况没有统一所有者。若直接拆 Vue 文件，业务判断会分散并形成更隐蔽的耦合。

## 决策

采用“两层治理”而不是一次性引入外部项目管理或 GIS 平台：

1. 根目录 `AGENTS.md`：所有修改前必读的强制开发规则、模块边界与门禁。
2. `docs/architecture/project-structure-and-low-coupling-contract-v1.md`：项目领域的状态、Read Model、资产、活动、存储和交付契约。

Project Hub 的第一阶段不是重写页面，而是先冻结并实现后端 Read Model：`ProjectOverviewView`、`ProjectAssetView`、`readiness`、`capabilities`、`blockers` 和 `next_actions`。前端只消费该契约。

## 设计原则

- 生命周期、准备情况、资产状态和任务状态严格分层；任何一个模块不得用自己的 `status` 替代其他层。
- 规则在后端集中计算；前端负责展示和导航，不复制领域判断。
- 新资产先经统一读取 DTO 暴露；不为统一界面而提前复制数据或新建泛化资产表。
- 存储路径属于服务端基础设施，不是浏览器参数或 UI 状态。
- 现有 API、`Project.status`、Worker 状态和历史文件路径优先保持兼容；聚合读取接口先增量增加。
- 重构按职责和可测试边界拆分，不按视觉区域或文件大小机械拆分。

## 备选方案与取舍

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| 仅拆 `ProjectWorkspace.vue` | 不采用 | 状态判断仍分散，无法降低业务耦合 |
| 引入 OpenProject/GeoNode/MapStore | 不采用 | 系统重叠、部署成本和迁移范围远超当前横向项目需要 |
| 后端 Read Model + DTO + 渐进式页面拆分 | 采用 | 可保持接口兼容，先解决规则所有权，再改善页面维护性 |

## 验收条件

1. 每次开发前都能从根目录发现并读取 `AGENTS.md`。
2. 项目生命周期、readiness、资产状态与任务状态在文档和后续 API 中有唯一语义。
3. `/overview` 的 blocker、capability 和下一步动作均由后端计算，前端不重复推理。
4. 资产页面只使用 `ProjectAssetView`，不依赖底层表名和服务器物理路径。
5. 导出和项目配置快照的边界对用户清晰可见，且导出来源可追溯。
6. 前端拆分不会增加同一项目概览的重复请求，也不会改变既有项目 CRUD 的兼容行为。

## 计划拆分

后续实施必须拆为独立、可验证的计划：

1. Project Hub 状态与资产 Read Model；
2. Miner 驾驶舱与资产目录；
3. 活动、导出和配置快照收口；
4. 跨模块契约测试与发布验收。

这份设计不授权直接全仓重构、改模型或改变部署拓扑。每一项实施前都必须先形成逐文件的实施计划和测试路径。
