# YunNan 矿山生态修复遥感解译系统

本仓库管理系统源码、部署配置、测试和项目文档。

开发或修改前请先阅读项目级 [AGENTS.md](./AGENTS.md)。项目的模块边界、状态模型和低耦合契约见 [docs/architecture/project-structure-and-low-coupling-contract-v1.md](./docs/architecture/project-structure-and-low-coupling-contract-v1.md)。

## 目录

- backend：Flask 后端、项目空间服务和推理任务。
- frontend：现有 Vue GeoView 解译前端。
- miner：项目工作台及其 Node/Vite 服务。
- docker：离线部署与容器运行脚本。
- docs：设计、部署、测试和实施计划。

## 不纳入版本库的运行资产

模型权重、原始影像、项目数据、推理输出、离线镜像、Docker 卷及本地密钥均由 .gitignore 排除。部署时请按 docs 中的离线部署说明挂载对应资产，不要将其提交到 GitHub。

当前开发中的分类成果矢量化与 GeoView 编辑方案见：
docs/superpowers/plans/2026-09-04-classification-vectorization-and-geoview-editor.md。
