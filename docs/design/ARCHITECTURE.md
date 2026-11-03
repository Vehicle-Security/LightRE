# LightRE 总体架构

本文以当前代码为准说明模块职责、数据归属和运行边界。当前命令和 API 见 [USAGE](../USAGE.md)；未实现的差分和 VM 恢复方案见 [future](../future/README.md)。

## 运行链路

```text
lightre <SO 或已有 workspace>
      │
      ├─ lightre.server
      │    ├─ 依赖检查
      │    ├─ prepare_analysis（导入 SO、自动静态导出或复用结果）
      │    ├─ WorkspaceReader（只读）
      │    ├─ main_workflow
      │    └─ Uvicorn / FastAPI
      │
      ├─ recovery
      │    ├─ 函数候选、Knowledge 和源码
      │    └─ 静态批次队列
      │
      └─ rehost
           ├─ 空 harness、契约和静态检查
           ├─ Java/Maven/unidbg Run
           └─ Codex 修复循环
```

根目录 `lightre` 是 POSIX shell 引导器，在 Ubuntu 24.04 基础容器中先用 apt 安装下载工具和证书，再安装 uv，并由 uv 准备 Python 与项目依赖。服务层继续准备原生工具、匹配 Ghidra 要求的 JDK 和独立 Codex CLI；无需调用者预装这些工具。

服务启动时接收 SO 路径、含一个 SO 的外部 workspace，或已有 SO 分析目录。`main_workflow.prepare_analysis()` 为 SO 自动创建仓库外的 workspace（也可由 `--workspace` 指定），导入目标并在需要时调用 `analyzers.ghidra.analyze()` 完成静态导出；已有完整结果直接复用，未完成的导出从原始 SO 重试。得到只读 `WorkspaceReader` 后再启动后续任务与 HTTP 服务。

## 模块边界

| 模块 | 当前职责 |
| --- | --- |
| `src/lightre/` | 服务入口、REST API、workspace 读取、依赖准备和顶层 workflow |
| `src/agents/` | Codex session/run、SQLite 状态、后台 worker、事件和最终回复 |
| `src/analyzers/` | Ghidra Headless 导出函数、范围、汇编和参考伪代码 |
| `src/recovery/` | 候选 C、源码模块、Knowledge、评论队列和批量恢复 |
| `src/rehost/` | harness 指令、契约、Maven/Java 执行、Run 快照、重放和修复循环 |
| `src/webui/` | 函数目录、汇编、候选、Knowledge 和评论页面 |
| `samples/minilua/` | 样本源码、Android 薄接口和样本自己的构建/测试脚本 |

原来的根目录脚本、多命令 CLI、`src/lightre/cli/` 和 `src/execution/` 已删除，不是当前架构的一部分。

## Agent 层

`AgentManager` 只负责模型执行生命周期，不理解 RVA、SO、JNI、harness 或验证语义：

- `AgentSession` 表示一个可持久化 Codex 会话，消息按提交顺序排队。
- `AgentRun` 表示一条消息的执行，可用 `poll()`、`iter_states()` 和 `wait()` 观察。
- SQLite 保存 session/run 状态、租约、取消请求和 Codex thread ID。
- prompt、事件 JSONL、stderr、最终正文和执行记录保存在 session/run 目录。
- 不同 session 可以并行；同一个 session 同时只允许一个 worker。

Recovery 目前仍有自己的批次调度和 Codex 调用协议；Rehost 已通过 `agents.run_blocking()` 接入，但两者的统一迁移尚未完成。

## Workspace 和数据归属

每个 SO 使用独立的外部目录：

```text
<workspace>/<name>-<sha>/
  index.sqlite                  # 元数据、函数目录和汇编索引
  binary/original.so            # 不可变原始目标
  references/ghidra/            # Ghidra 项目、导出和日志
  source/src/                   # 当前源码正文
  knowledge.md                  # 当前逆向知识
  harness.json                  # 动态 harness 关联
  tasks/<id>/                   # 静态任务或 harness 修复任务
  runs/<id>/                    # 构建、执行、观察和验证证据
```

SQLite 是函数和汇编目录的权威来源；源码文件是候选正文的权威来源；Knowledge、任务和 Run 保留在各自的文件记录中。原始 SO、固定用例、检查器和历史 Run 不由模型覆盖。

## Rehost 运行边界

Rehost 只支持 Android ARM64 SO + Java/Maven/unidbg。`Contract` 保存固定输入、预期、检查器和产物声明；`runner` 为每次执行建立完整快照；`process` 记录命令、日志、超时和进程组清理；`loop` 在外部验证失败后安排下一轮 Agent。

没有独立预期时只保存 `observed` 结果。观察、构建成功、检查器通过和语义等价是不同状态，不能混用。

## 服务和 UI

`lightre.api` 提供健康、workspace、workflow 和 Recovery 状态接口；`webui` 提供源码浏览和评论接口。主 workflow 当前写入 `main-workflow.json`，并启动 Recovery 与 Rehost；差分反馈和用户指导聊天尚未接通。

主服务重启后的任务恢复、Rehost 重复启动保护和 Recovery/Agent 状态统一仍属于后续整理工作，不应由文档提前宣称已经完成。
