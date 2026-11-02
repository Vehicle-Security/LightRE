# LightRE

LightRE 面向 ARM64 SO 的逆向分析，产品定位是容器内持续运行的服务：提供一个用户指导聊天窗口，并在后台管理多个 Codex CLI 工作会话，自动推进调查、恢复与验证。长期目标是恢复 VM 混淆中的业务逻辑，交付可编译、可独立执行、可继续维护的源码工程。

常驻服务、指导聊天与后台编排正在按 [V0.31 计划](docs/milestones/v0.31/PLAN.md#维护与优化计划)整理，尚未接通。当前代码仍使用专用 CLI 和源码浏览 Web；Python 环境由 uv 管理，现有启动方式见 [功能说明](docs/USAGE.md#agent-入口)。项目中的 prompt/workflow 为内部工作指令，[SKILL.md](SKILL.md) 暂作旧调用方式兼容。

当前静态流程使用 Ghidra 导出函数、完整汇编和参考伪代码，再由 Codex 生成一轮保守候选；已有工作区支持直接从完整汇编生成候选。Web 用于查看同一份源码、知识和任务，提交修改建议。候选允许保留 goto、平坦化、MBA 和已注明的 Ghidra 局限，未经动态等价验证。

动态执行以 [rehost workflow](docs/USAGE.md#rehost-从零生成) 调查未知 Android ARM64 SO，从空目标工程生成 unidbg harness，并根据真实运行逐项补环境。工具复用构建、Run 快照、重放及失败交接；不复制预制目标 Java harness。没有独立预期时只收集观察，固定用例/检查器存在时进行外部核验。DG/minilua 旧单边运行记录保留为历史证据，不代替新 workflow 的真实集成验证。

| 范围 | 状态 |
| --- | --- |
| V0.2 静态候选与 Web 审阅 | 2026-11-01 已通过用户人工验收 |
| V0.31 通用 harness 与修复循环 | 开发实现及实跑已有记录，用户继续验收中 |
| 常驻服务、指导聊天窗口与多工作会话整合 | 已明确为 V0.31 收尾方向，待实施和验证 |
| 候选 SO 构建、双边差分、VM 语义恢复、完整工程 Goal Loop | 尚未交付 |

DG 巨函数 0x1c07c 仍无完整 C 候选，按用户决定留到后续差分阶段处理；原 VM 能运行不表示该恢复问题已经解决。

目标交互是在 LightRE 指导窗口中提交目标、了解进度、调整任务，由服务器创建后台工作会话并核验实际产物。关闭窗口不会取消已提交工作；当前 CLI/兼容 Agent 入口继续按功能说明使用，不能将现有源码浏览页面当成指导聊天已实现。

分析产物写入所选外部 workspace，每个 SO 独立保存 SQLite、原始副本、源码、知识与运行记录。系统依赖统一通过 scripts/install_deps.sh 准备；不要求为使用工具逐轮确认内部步骤。

- [当前功能、命令与 API](docs/USAGE.md)
- [文档入口与职责](docs/README.md)
- [后续路线](docs/ROADMAP.md)
- [V0.3 第三阶段计划：unidbg 运行与差分闭环](docs/milestones/v0.3/PLAN.md)
- [V0.31 验收范围](docs/milestones/v0.31/PLAN.md)与[真实检查记录](docs/milestones/v0.31/DELIVERY.md)
- [VM 分析方法](docs/design/VM_RECOVERY.md)

日常开发检查使用轻量临时数据，不启动浏览器、完整分析或模型任务。样本启动 Markdown 只用于人工指定目标：[DG](tests/dg_start.md)、[minilua](tests/minilua_start.md)。
