# V0.3：统一 Agent 与自动 Rehost 主循环

状态：当前开发版本，尚未完成最终验收。

V0.3 合并原第三阶段计划与 V0.3 增量的有效范围，后续文档统一使用 V0.3 作为版本名称。本版本的目标是把 LightRE 从“多个独立脚本和模型调用”收敛为一个在线服务：统一底层 Agent、自动生成 rehost harness，并由主 workflow 持续组织 Recovery、Rehost 和后续差分。

## 版本目标

1. **统一 Agent runtime**
   - 所有 Codex 调用经过 `src/agents/`。
   - 业务模块只提交初始 prompt、memory、knowledge 和模块消息。
   - Agent runtime 负责 session、run、上下文、完整结果、恢复和迭代消息。
   - Recovery、Rehost、Checker 和未来差分 Worker 不再自行管理 Codex 生命周期。

2. **在线服务入口**
   - 唯一产品入口为：

     ```bash
     lightre <workspace>
     ```

   - 服务提供 REST API、内置 Web UI 和用户 Agent 聊天入口。
   - 用户不调用 Recovery、Rehost、Harness 或 Agent 子命令。

3. **主 workflow**

   ```text
   启动 workspace
     ├─ Recovery：静态分析、源码恢复、共享 knowledge
     └─ Rehost：从零调查 SO、生成 unidbg harness、维护专属 knowledge

   harness 可执行
     → 候选构建与双边差分接口
     → 失败分类
     → harness 问题反馈 Rehost
     → 源码/语义问题反馈 Recovery
     → 继续循环
   ```

4. **通用 Rehost**
   - 只面向 ARM64 + unidbg。
   - 每个目标从空 harness 开始，不维护 DG/minilua 专用模板。
   - Worker 通过统一 Agent 修改 harness。
   - Verifier 负责真实构建、运行、固定契约和 Checker Agent 审查。
   - Rehost 保存独立 knowledge，可引用共享逆向知识但不覆盖它。

5. **依赖与部署**
   - 开发环境工具放在仓库 `tools/`。
   - 安装环境工具放在 `~/.local/lightre/tools/`，可由 `LIGHTRE_TOOLS_DIR` 覆盖。
   - `lightre.deps` 负责检查、确定性安装和修复；缺失依赖可进入 Dependency Agent 修复循环。
   - 运行中的 Agent 不直接修改分析 workspace 以外的系统状态，安装记录必须持久化。

## 交付拆分

| 阶段 | 交付 | 完成条件 |
| --- | --- | --- |
| A | Agent runtime 收敛 | Recovery、Rehost、Verifier 均经过 `AgentTask`；保存 prompt、上下文、run、完整结果并可恢复 |
| B | 服务与 REST API | `lightre <workspace>` 启动服务；REST API、Web UI 和主 workflow 共存 |
| C | 主 workflow | Recovery 与 Rehost 自动启动；状态写入 workspace；失败结果可分类并形成下一轮消息 |
| D | Rehost 闭环 | 从零生成通用 unidbg harness；真实运行失败可自动交给 Worker 修复；Verifier 决定停止 |
| E | 差分接入 | 接入候选构建、原实现/候选运行和报告接口；差分失败能反馈到 Recovery 或 Rehost。完整 VM 语义恢复不属于本版本完成条件 |
| F | 依赖自修复 | 检查缺失依赖，确定性安装后仍失败时启动 Dependency Agent，直到成功或记录不可修复原因 |

## 数据边界

- `source/src/`：唯一当前源码。
- `knowledge.md`：Recovery、候选构建和差分共享知识。
- `rehost/knowledge.md`：ABI、初始化、补环境和 harness 失败知识。
- `tasks/`、`runs/`：每轮 prompt、结果、日志、验证和证据。
- Agent 数据库存储 session/run 生命周期；业务状态仍由主 workflow 和各模块保存。

## 明确不纳入

- 不再维护 `scripts/` 或 `src/lightre/cli/` 产品入口。
- 不实现完整 VM、OLLVM、MBA 自动恢复。
- 不扩展 Qiling、其他架构或多后端执行。
- 不要求用户逐轮指导核心恢复；用户指导只用于完成后的可读性优化。

## 验收

- 从一个外部 workspace 执行 `lightre <workspace>`，服务能自动启动 Recovery 和 Rehost。
- 所有模型调用均可在 agents 数据和业务任务中追溯。
- Rehost 能从空工程生成并验证一个 ARM64 unidbg harness，失败后自动续做。
- Recovery 与 Rehost 共享源码和通用逆向知识，但保留各自知识边界。
- 依赖缺失能够自动修复，或保存完整失败原因和下一步。
- 差分接口能够接收 harness 运行结果并产生下一轮模块消息。
