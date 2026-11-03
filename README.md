# LightRE

LightRE 面向 Android ARM64 SO，结合 Ghidra 静态导出、Codex Agent 和 Java/unidbg 动态运行，帮助调查目标入口、恢复候选源码并保存可复现的运行证据。

当前开发版本是 **V0.3**。本版本的验收重点是统一 Agent runtime，以及从空工程生成、运行、修复和重放 unidbg harness。服务入口和主 workflow 已有实现，但仍在收敛，完整双边差分、业务等价验证和 VM 语义恢复不属于当前完成条件。

## 使用方式

需要保证codex-cli可用，本项目不用另外配置api-key。

唯一产品入口是：

```bash
./lightre <目标资料目录或已有 workspace>
```

目标资料目录可以包含一个或多个已解包的 ARM64 `.so`，也可以包含 APK、DEX、依赖库和其他调查资料。LightRE 会选择目标 SO、创建或复用外部 workspace，并在后台完成静态导出及后续任务。

也可以直接指定 SO：

```bash
./lightre /data/libtarget.so --workspace /data/libtarget-analysis
```

首次启动，只是安装依赖但不启动分析：

```bash
./lightre --prepare
```

默认服务监听 `0.0.0.0:8000`。开发时可以覆盖监听地址和端口：

```bash
./lightre /data/target --host 127.0.0.1 --port 9000
```

服务启动后提供：

- Web UI：<http://localhost:8000/>
- API 文档：<http://localhost:8000/docs>
- 启动与分析状态：`GET /api/status`
- workspace、workflow 和 Recovery 状态接口

默认 workspace 位于启动时当前目录下的 `workspace/`；使用 `--workspace` 可以指定其他位置。样本 Git 仓库内部不能作为 workspace，LightRE 仓库根目录也不能直接用来保存分析结果。

## 安装和依赖

源码启动器会优先使用项目环境或 PATH 中的 `uv`；空的 Ubuntu/Debian 容器可以在启用自动安装时自行准备 `uv` 和系统依赖。

已有 wheel 时：

```bash
uv tool install ./dist/lightre-*.whl
lightre path/to/target
```

源码开发：

```bash
uv sync --locked
./lightre path/to/target
```

静态分析和动态 Rehost 需要 Java/JDK、Maven、Clang/LLD、`clang-format`、`readelf`、Ghidra，以及已经认证的 Codex CLI。服务会按依赖 profile 检查这些工具；设置 `LIGHTRE_AUTO_INSTALL_DEPS=0` 可关闭自动安装。

常用环境变量：

| 变量 | 作用 |
| --- | --- |
| `LIGHTRE_TOOLS_DIR` | 受 LightRE 管理的工具目录 |
| `GHIDRA_HOME` | 已有 Ghidra 安装目录 |
| `LIGHTRE_CODEX_BIN` | Codex CLI 路径，默认从 PATH 查找 |
| `LIGHTRE_AUTO_INSTALL_DEPS=0` | 只检查依赖，不自动安装 |
| `LIGHTRE_HOST` / `LIGHTRE_PORT` | 服务默认监听地址和端口 |

Codex CLI 的认证和配置由部署环境提供，不写入分析 workspace。

## Workspace 产物

每个 SO 版本使用独立的分析目录：

```text
<workspace>/<sample>-<sha256-prefix>/
  index.sqlite          # 函数目录和汇编索引
  binary/original.so    # 原始 SO 副本
  references/ghidra/    # Ghidra 导出、项目和日志
  source/src/           # 当前候选源码
  knowledge.md          # Recovery 与后续分析共享知识
  rehost/               # 静态检查和 Rehost 资料
  tasks/                # Agent、Recovery 和 Rehost 任务
  runs/                 # 构建、运行、快照和验证记录
```

任务会保存 prompt、完整 Agent 返回、事件、stderr、源码快照、输入、依赖哈希和 `verification.json`。恢复任务时会重新检查固定 SO、契约、用例和保护文件，不沿用未经重新验证的旧通过结果。

## 当前能力

- **Agent runtime**：统一管理 Codex session/run，持久化状态、提示、事件、结果、取消、暂停、恢复和跨进程 worker。
- **Rehost**：从空的 Maven/unidbg 工程开始调查入口、ABI、初始化和环境，支持构建、真实执行、Run 重放和失败修复循环。
- **Recovery**：根据 Ghidra 参考伪代码或完整汇编生成候选源码，维护函数目录、Knowledge 和恢复队列。
- **静态分析**：保存函数、完整 ARM64 汇编、参考伪代码及导出日志；支持 workspace 复用和重新导出。
- **Web/API**：提供 workspace、函数、候选源码、汇编、Recovery 和任务状态的浏览接口。

Rehost 的动态后端当前限定为 **ARM64 + Java/Maven/unidbg**。没有独立预期时，运行结论只能是 `observed`；真实执行成功不等于候选源码已经等价。

## V0.3 范围

本版本保留：

1. Agent runtime 收敛，业务模块通过统一 Agent 接口调用 Codex。
2. Rehost 从零生成 harness，并由程序化 Verifier 驱动构建、运行和修复。
3. 任务、Run、输入、依赖、源码和验证证据可恢复、可重放。

本版本暂不承诺：

- 候选构建与原实现/候选的双边差分；
- 完整 VM、OLLVM 或 MBA 自动恢复；
- Qiling、其他架构或其他动态后端；
- 一次性恢复整个 SO 或完整业务语义；
- 真实目标上的通用自动 Rehost 已完成验收。

版本范围和证据以 [V0.3 文档](docs/milestones/v0.3/README.md)、[开发计划](docs/milestones/v0.3/PLAN.md) 和 [交付记录](docs/milestones/v0.3/DELIVERY.md) 为准。

## 开发与测试

准备环境并运行测试：

```bash
uv sync --locked
uv run --locked python -B -m unittest discover -s tests -p 'test_*.py' -q
```

当前主测试门禁包含 211 项测试，其中 4 项按环境条件跳过，覆盖 Agent runtime、Recovery、静态导出、Rehost、契约重放、workspace、启动器、目标探索、服务状态和打包资源。测试使用临时 workspace、子进程替身和固定 checker；真实 Codex、发布包及干净容器检查的运行方式见[使用指南](docs/USAGE.md#检查)。

构建 wheel：

```bash
uv build --wheel
```

项目不再维护旧的 `scripts/`、`src/execution/` 或多命令 CLI 入口。内部 Python API 仍可供开发和 workflow 使用，但用户入口统一为 `lightre <目标>`。

## 文档

- [当前功能与 API](docs/USAGE.md)
- [总体架构](docs/design/ARCHITECTURE.md)
- [Agent 架构说明](docs/design/AGENTS_ARCHITECTURE_EXPLAINED.md)
- [Rehost workflow](docs/design/REHOST_WORKFLOW.md)
- [Recovery 与任务循环](docs/design/GOAL_LOOP.md)
- [需求清单](docs/REQUIREMENTS.md)
- [长期路线图](docs/ROADMAP.md)
- [完整文档索引](docs/README.md)
