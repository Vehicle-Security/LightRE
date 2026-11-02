# 技术设计

本目录集中维护架构、实现协议和后续技术方案。日常使用见[功能说明](../USAGE.md)，产品范围见[需求清单](../REQUIREMENTS.md)，开发顺序见[路线图](../ROADMAP.md)，验收状态和历史证据见[文档入口](../README.md)。

| 文档 | 内容与边界 |
| --- | --- |
| [总体架构](ARCHITECTURE.md) | 常驻服务、指导聊天与工作会话，以及模块职责、数据归属和 workspace/harness 边界 |
| [Goal Loop](GOAL_LOOP.md) | 长期工程恢复、完整返回交接与停止条件；已实现最小 harness 子集，完整闭环仍属规划 |
| [动态差分](DIFFERENTIAL_EXECUTION.md) | 候选构建、状态映射与双边比较的后续方案 |
| [源码导航](SOURCE_NAVIGATION.md) | 当前身份标记与一对一导航协议，以及未来多对多发布设计 |
| [任务队列](TASK_QUEUE.md) | 已实现的静态候选与评论调度、锁、持久化及恢复协议 |
| [VM 恢复](VM_RECOVERY.md) | VM、平坦化、MBA 方法与 M0–M6 检查条件；不代表已交付语义恢复能力 |

设计正文须区分当前实现与规划；命令和 API 统一在 USAGE 维护，实测结果留在对应版本 DELIVERY。新增技术设计放在本目录，并更新此索引。
