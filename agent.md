# YunNan Agent 接手入口

请先完整阅读本仓库的 [AGENTS.md](AGENTS.md)，这是统一维护的项目级规则。

随后按任务阅读 [开发规范](docs/development-standard.md) 的模块、性能和测试章节，以及对应架构/API 契约。

接手前核对当前目录、分支、提交与未提交改动；不要假设前一次聊天、另一个工作树或 `main` 已包含当前功能。

负责人下任务和换 Agent 的模板见 [新生与 Agent 协作手册](docs/agent-collaboration-guide.md)。

本文件只提供阅读入口，不维护第二套规则，也不假设所有 Agent 工具都会自动加载它。

## 审查与测试优先方法（2026-09-20 登记）

今后对本仓库做 code review 与测试时，**优先采用 [alibaba/open-code-review](https://github.com/alibaba/open-code-review)**（CLI `ocr`；无 LLM 配置时用委托模式 `ocr delegate rule <files>` 取规则、宿主 agent 执行审查），辅以 [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) 五轴审查与验证守则。完整流程、severity 分级与证据规则见 [docs/code-review/methodology.md](docs/code-review/methodology.md)，本轮审查记录见 [docs/code-review/20260920-audit.md](docs/code-review/20260920-audit.md)。
