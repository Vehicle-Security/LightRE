# 文档入口与职责

先按问题选择文档。版本计划决定本次开发范围，专题设计说明具体机制，交付记录说明已经验证到哪里；长期设计存在不代表能力已经实现。V0.2 已于 2026-11-01 通过用户人工验收，V0.31 是下一小步计划，V0.3 保留为后续完整闭环的总体方案。

## 本次开发先读什么

- **使用当前功能**：[V0.2 使用说明与 API](milestones/v0.2/USAGE.md)。
- **核对当前版本范围和结果**：[V0.2 计划](milestones/v0.2/PLAN.md)、[交付记录](milestones/v0.2/DELIVERY.md)。
- **开发 V0.31**：[本版计划](milestones/v0.31/PLAN.md) → [loop 的结果保存与交接](GOAL_LOOP.md#最小落地先完整保存返回正文) → [harness 的位置与复用](LLM_DECOMPILER_DESIGN.md#agent-生成与共享-unidbg-harness)。先完成简单 SO 的 harness 和该任务的修复循环。
- **了解后续顺序**：[路线图](IMPLEMENTATION_PLAN.md)与 [V0.3 总体方案](milestones/v0.3/PLAN.md)。DG 使用现有 `.bin`，minilua 使用 `.lua`；真实目标适配、候选构建和双边差分继续分步推进。

无需每次开发都通读全部长期设计；按当前问题再读取相关专题与需求编号。

## 设计文件分别负责什么

下表列出规则的主要维护位置。其他文档保留概述和引用，具体规则变更在所属文档修改，避免到处复制同一套细节。

| 文件 | 负责的问题 | 与其他文档的边界 |
| --- | --- | --- |
| [REQUIREMENTS.md](REQUIREMENTS.md) | 产品需要哪些可验收行为、需求 ID、人工验收状态 | 人工维护需求口径；具体实现方法进入设计，当前小版本范围进入 PLAN |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | 能力引入顺序 D1–D4、版本入口与交付原则 | 不重复维护各版本的参数、命令和验收清单 |
| [LLM_DECOMPILER_DESIGN.md](LLM_DECOMPILER_DESIGN.md) | 总体架构、工作区与持久数据归属、共享 harness、VM/OLLVM/MBA 总体分析方法 | 负责模块怎样配合；loop、差分和源码导航的具体协议引用对应专题 |
| [GOAL_LOOP.md](GOAL_LOOP.md) | 多轮执行、每轮记录、context 交接、实际核验、取消与续做 | 决定如何持续推进任务；比较哪些语义结果由差分专题规定 |
| [DIFFERENTIAL_EXECUTION.md](DIFFERENTIAL_EXECUTION.md) | ARM64 unidbg 执行、用例与状态、结果比较、候选产物及 minilua 适配 | 决定怎样运行与比较；不另定义任务循环或产品版本顺序 |
| [VM_RECOVERY_MILESTONES.md](VM_RECOVERY_MILESTONES.md) | 单个目标从 M0 入口到 M6 业务源码的分析进展与检查条件 | M0–M6 是样本分析阶段，D1–D4 是工具能力顺序，均不等于产品版本号 |
| [SOURCE_NAVIGATION.md](SOURCE_NAVIGATION.md) | 源码身份、标记、命名、地址映射、调用跳转及索引发布 | 维护源码定位专题的已实现范围与后续协议，不改变逆向任务的完成条件 |
| [ARM64_RE_R&D.md](ARM64_RE_R%26D.md) | 通用 ARM64 逆向教程与工具背景 | 背景参考；其中 Frida/LLDB 等例子不代表 LightRE 当前实现或开发范围 |

## 版本目录分别负责什么

| 文件 | 用途与状态 |
| --- | --- |
| [v0.1/DELIVERY.md](milestones/v0.1/DELIVERY.md) | V0.1 的历史交付范围和现场记录；当时的 Rizin 流程不作为当前默认入口 |
| [v0.1/USAGE.md](milestones/v0.1/USAGE.md) | V0.1 历史用法，供复核旧版本 |
| [v0.2/PLAN.md](milestones/v0.2/PLAN.md) | V0.2 的范围与验收条件，已通过用户人工验收 |
| [v0.2/DELIVERY.md](milestones/v0.2/DELIVERY.md) | 实际实现、实跑结果、缺口和历史记录；按正文标记区分当前与历史 |
| [v0.2/USAGE.md](milestones/v0.2/USAGE.md) | 当前功能的操作、参数、目录与本地 API |
| [v0.2/TASK_QUEUE.md](milestones/v0.2/TASK_QUEUE.md) | 已有批量分析与评论队列的持久化、调度和状态；不是工程 Goal Loop |
| [v0.3/PLAN.md](milestones/v0.3/PLAN.md) | 单函数差分与语义修复的总体方案，分步实现，不是下一小版本的全部交付范围 |
| [v0.31/PLAN.md](milestones/v0.31/PLAN.md) | 下一个小版本：最小 unidbg harness 与原文交接的修复循环，尚未实现 |

## 修改与核对规则

需求编号和人工验收口径维护在 REQUIREMENTS；本次做哪些事维护在对应版本 PLAN；运行机制维护在专题设计；实际结果和限制写 DELIVERY；已实现的命令/API 写 USAGE。源码中的实际行为仍需核对，不能以设计文字代替运行证据。

用户的新指示优先。设计与版本计划冲突时明确记录并同步修正相关概述，不按文件修改时间猜测；长期能力不自动扩大当前小版本范围，历史交付文档不覆盖当前版本状态。调整方法不自动勾选人工验收项。

功能内的可执行指令放在 [src/recovery/prompts](../src/recovery/prompts/)；例如 [恢复指令](../src/recovery/prompts/RECOVER_AND_REFINE.md)和 [构建验证 workflow](../src/recovery/prompts/BUILD_AND_VERIFY.md)。它们是待接入的功能资源，docs 保存说明与设计，不复制一套运行时 prompt。
