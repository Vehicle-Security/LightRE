# 文档入口

当前功能、启动方式和 API 只维护在 [USAGE.md](USAGE.md)。设计文档区分已经存在的代码和未来能力；版本目录只保存范围、验收条件和历史证据。

| 内容 | 文档 |
| --- | --- |
| 当前启动、workspace、API 和 Python 接口 | [USAGE.md](USAGE.md) |
| 产品需求 | [REQUIREMENTS.md](REQUIREMENTS.md) |
| 长期能力顺序 | [ROADMAP.md](ROADMAP.md) |
| 当前模块和数据边界 | [design/ARCHITECTURE.md](design/ARCHITECTURE.md) |
| Prompt 的模块归属、模板变量和发布规则 | [design/PROMPTS.md](design/PROMPTS.md) |
| Recovery 全部模板、内部 Skill、触发条件和输入快照 | [design/RECOVERY_PROMPTS.md](design/RECOVERY_PROMPTS.md) |
| Agent、任务队列和 Goal Loop | [design/README.md](design/README.md) |
| 后续差分、VM 恢复和主循环设计 | [future/README.md](future/README.md) |
| 版本范围和验收证据 | [milestones/](milestones/) |

当前版本基线是 V0.3，仍等待最终人工验收。V0.2 静态候选和函数浏览已通过人工验收。版本状态不能由轻量测试自动推进。

## 文档规则

- 当前命令和 API 只写在 `USAGE.md`。
- 设计文档只描述模块边界、数据协议和未交付方案，不复制操作手册。
- 历史交付记录保留当时的事实，但不保留已经删除入口的可执行命令。
- 删除或移动文档时同步修复链接；仓库不维护按样本名称复制的旧 harness 手册。
