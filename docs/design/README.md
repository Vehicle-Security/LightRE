# 技术设计

本目录集中维护架构、实现协议和后续技术方案。日常使用见[功能说明](../USAGE.md)，产品范围见[需求清单](../REQUIREMENTS.md)，开发顺序见[路线图](../ROADMAP.md)，验收状态和历史证据见[文档入口](../README.md)。

## 当前实现（V0.3）

| 文档 | 内容与边界 |
| --- | --- |
| [总体架构](ARCHITECTURE.md) | 常驻服务、指导聊天与工作会话，以及模块职责、数据归属和 workspace/harness 边界 |
| [Agent 架构](AGENTS_ARCHITECTURE_EXPLAINED.md) | agents 库设计、持久状态和非阻塞生命周期 |
| [Goal Loop](GOAL_LOOP.md) | 长期工程恢复、完整返回交接与停止条件；已实现最小 harness 子集，完整闭环仍属规划 |
| [源码导航](SOURCE_NAVIGATION.md) | 当前身份标记与一对一导航协议，以及未来多对多发布设计 |
| [任务队列](TASK_QUEUE.md) | 已实现的静态候选与评论调度、锁、持久化及恢复协议 |
| [Prompt 资源](PROMPTS.md) | 各模块的指令归属、Jinja2 模板与递归打包规则 |
| [Recovery Prompt 映射](RECOVERY_PROMPTS.md) | 两个任务 Prompt、一个内部 Skill 与一份汇编参考的调用和留档 |
| [Rehost 知识库](REHOST_KNOWLEDGE.md) | HARNESS.md / workspace 知识库 / 轮次快照三层经验的职责与注入方式 |
| [Rehost 模块设计](REHOST_DESIGN.md) | 入口、分层、各文件职责、数据目录与关键约束 |

## 未来功能

规划中的功能设计已移至 [future/](../future/)，包括动态差分执行、VM 语义恢复和主 Workflow。

设计正文须区分当前实现与规划；命令和 API 统一在 USAGE 维护，实测结果留在对应版本 DELIVERY。新增技术设计放在本目录，并更新此索引。
