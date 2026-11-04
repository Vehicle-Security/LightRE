# 当前功能说明

本文是 LightRE 当前版本唯一的操作和 API 说明。产品入口是常驻服务；内部模块可以通过 Python API 调用，不能再使用旧的多命令 CLI。

## 安装和启动

LightRE 面向可联网、具有 root 权限的 Ubuntu 24.04 基础容器。无需预装 curl、uv、Python 或原生分析工具，在仓库根目录指定目标资料目录：

```bash
./lightre ../target-dir
```

目标目录包含已解包的 ARM64 SO，以及可选的 APK、DEX、资源和依赖库。LightRE 递归查找 ARM64 SO；多个候选时由 Codex 只读探索，结合资料选择主 SO 和依赖。选择依据与探索记录保存在 workspace，后续启动复用选择。

启动器先通过 apt 自动安装缺少的下载工具与 CA 证书，再安装 uv，并由 uv 准备 Python 3.12 和项目依赖。已安装 wheel 时直接使用 `lightre <目标目录>`。服务接受 `--host`、`--port`，默认监听 `0.0.0.0:8000`；环境变量 `LIGHTRE_HOST` 和 `LIGHTRE_PORT` 可以提供默认值。

服务启动时检查并自动准备 `analysis` 依赖，包括 JDK、Maven、Clang/LLD、Ghidra 和独立 Codex CLI。JDK 版本按已安装的 Ghidra 要求选择，Ghidra 缺少 ARM64 原生组件时自动构建。设置 `LIGHTRE_AUTO_INSTALL_DEPS=0` 可禁止自动安装；非 root 部署需要免交互 sudo。模型访问凭据由部署配置提供，例如挂载 Codex 配置和认证资料。

`./lightre --prepare` 会安装并检查完整依赖，输出报告后退出，不启动分析或模型任务。

## workspace

服务默认使用启动时当前目录下的 `workspace/`。可通过 `--workspace` 自选输出位置，目录不存在时自动创建，已存在时继续使用；样本 Git 仓库内部不能作为 workspace，LightRE 仓库根目录也不能直接用来保存分析结果：

```bash
./lightre ../target-dir --workspace ../analysis-workspace
```

无需提前创建目录或复制 SO。完成依赖准备后先启动 HTTP 服务，目录探索及 Ghidra 静态导出在后台运行；页面通过始终可用的 `GET /api/status` 展示任务状态，不展示 Ghidra 日志。静态分析完成后自动启动或恢复 Recovery，页面自动加载函数并持续显示处理数量、候选数量和队列进度，随后显示 Rehost 状态。页面等 workspace 就绪后才请求函数列表；准备期间直接访问工作区相关 API 仍返回 HTTP 503。后台失败会在页面显示具体错误。首次启动自动保存原始 SO 副本并导出函数、完整汇编和参考伪代码。产物结构为：

```text
<workspace>/<sample>-<sha>/
  index.sqlite
  binary/original.so
  references/ghidra/
  source/src/
  knowledge.md
  tasks/
  runs/
```

`index.sqlite` 由工具自动创建和维护，元数据达到 `schema_version=2`、`state=ready` 后才交给后续任务。读取器是只读的，汇编按 `offset` 和 `limit` 分页读取。原始 SO、函数 RVA、汇编和源码索引以该 workspace 为准。

静态导出由主启动流程调用 `analyzers.ghidra.analyze()` 完成，无需手动执行 Python。已完成的静态结果直接复用；导出失败后重新运行相同启动命令，会从保存的原始 SO 重试。终端会显示 Ghidra 日志路径。

workspace 根目录的 `target.json` 保存输入目录相对位置、主 SO、依赖库和当前分析目录。每个 SO 的目录按 `<SO 文件名去掉扩展名>-<SHA-256 前缀>` 命名，前缀从 4 位开始，冲突时延长，复用时核对完整哈希。

再次执行相同的目标目录命令，会复用静态结果并继续原 Recovery 队列。Rehost 等待 Recovery 释放写锁后，继续已有任务、harness 和修复轮次；已完成、已观察或已取消的 Rehost 任务保留原状态。目标 SO 内容更新时按新哈希建立独立结果；原 SO 被移除时可使用保存的副本继续当前版本。重复启动同一目标的服务会报告已有实例。

服务也兼容直接指定 SO、已有 workspace 或 `<workspace>/<sample>-<sha>` 分析目录。旧 workspace 缺少 `target.json` 时自动打开唯一已有结果；若有多个结果，需指定其中一个分析目录。

分析结果必须放在 LightRE 仓库和样本 Git 仓库之外。

## 服务 API

服务根 API 当前提供：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/health` | 服务和 workspace 状态 |
| GET | `/api/workspace` | 样本元数据和函数目录 |
| GET | `/api/workflow` | 主流程及实时 Rehost 证据 |
| GET | `/api/recovery` | Recovery 队列和函数状态 |
| POST | `/api/recovery/resume` | 恢复或提交 Recovery 任务 |
| GET | `/api/rehost` | Rehost 轮次、步骤、构建和执行结果、运行输出摘要 |
| POST | `/api/rehost/retry` | 异步重试或重新验证，复用原 harness、固定用例和任务记录 |

函数浏览器由 `webui` 模块提供，服务挂载在 `/ui/`；浏览器内部接口包括 `/api/functions`、`/api/functions/{rva}/assembly`、`/api/functions/{rva}/candidate`、评论和任务控制接口。`POST /api/functions/{rva}/recover` 重新分析指定函数，包括已经有 LLM 候选的函数。页面读取保存结果，重新分析和评论由 Recovery 队列异步处理。

## Recovery

Recovery 处理函数候选和 Knowledge：

页面“任务详情”这一行提供 Recovery、Rehost 标签，标签内显示实时进展；单击栏或标签展开，点击标签切换面板，双击栏内任意位置收起。详情默认折叠，每次只展示一个模块；支持左右方向键切换标签，在栏标题上按 Enter 或空格切换展开状态。顶部工具栏仅保留工作区信息和主题切换。Recovery 标签显示正在处理的任务与数量，空闲时显示未解决、失败或 LLM 已处理数量；完整覆盖率放在 Recovery 面板中。Rehost 运行时显示轮次与实际步骤；仅返回时显示“待等价验证”，固定用例通过后显示“待差分逆向”。差分测试逆向阶段尚未接入，这些状态均不表示整体分析完成。

Recovery 区域提供“暂停队列”“恢复队列”和“分析剩余 N 个函数”；每条任务都能展开查看模型活动、处理结果和失败／未解决原因，点击函数可查看源码与分析上下文。Rehost 区域展示当前修复轮次与实际步骤（模型修改、编译、unidbg 执行、结果检查），以及编译结果、进程退出码、harness 报告的 native 返回、固定用例执行数量、报错和运行输出。native 返回与独立期望验证分开显示。

Rehost 失败或中断后可“重试 Rehost”；已完成或仅记录观察时可“重新验证”。按钮只提交 Rehost，保留既有 harness 与契约，从当前文件重新构建、运行和检查，失败时继续修复。接口立即返回 HTTP 202；已有协调任务执行或等待时返回 HTTP 409，避免重复提交。运行中约每 3 秒刷新 Rehost 状态。升级后需重启服务加载新的后端接口和进度记录逻辑。

升级 Prompt 不会自动改写已有分析结果，“分析剩余 N 个函数”跳过已有 LLM 候选的函数。要用当前规则改善旧结果，重启服务加载更新后，选中函数并点击“重新分析”；它保留当前代码和 Knowledge 作为输入，使用 `RECOVER.md` 创建定向任务，完成后刷新函数名称和源码。原始 catalog 名称和 RVA 不变，界面显示当前 C 名称。

```python
from lightre.workspace import WorkspaceReader
from recovery.jobs import progress, start_task

reader = WorkspaceReader('/absolute/path/to/sample-sha')
task = start_task(reader, input_mode='reference')
print(task)
print(progress(reader))
```

`input_mode='assembly'` 会让模型直接读取已保存的完整汇编，不使用 Ghidra 伪代码。批次按模块、函数数和指令数限制；单个超大函数独占批次。任务、提示、事件、错误、候选和 Knowledge 均保存在 workspace 中。

Recovery 当前仍使用自己的批次调度器；Agent runtime 的统一接入属于 V0.3 后续整理项。

## Agent runtime

`agents` 管理 Codex CLI 的持久 session/run。状态写入 SQLite，prompt、事件、stderr 和最终正文写入 session 目录：

```python
from agents import AgentManager

manager = AgentManager('/absolute/path/to/agents')
session = manager.create('/absolute/path/to/project')
run = session.submit('调查当前工程并记录结论')

for state in run.iter_states(timeout=600):
    print(state['status'])
result = run.result()
```

`submit()` 立即返回 `AgentRun`。调用方可以使用 `poll()`、`iter_states()` 或 `wait()`；`cancel()`、session 的 `pause()`、`resume()` 和 `cancel()` 用于控制生命周期。Codex CLI 由 `LIGHTRE_CODEX_BIN` 指定，默认从 PATH 查找 `codex`。

## Rehost

Rehost 从空的 Java/unidbg harness 开始调查 Android ARM64 SO。它不按 DG、minilua 或其他目标名称复制模板；目标入口、ABI、初始化、依赖和环境由每个 workspace 的任务资料记录。

```python
from rehost import start, resume

state = start(
    '/absolute/path/to/sample-sha',
    description='调查目标入口并生成可重放的 unidbg harness',
    inputs=['/absolute/path/to/seed.bin'],
    max_rounds=3,
)
print(state)
```

`rehost.workflow.start()` 会创建新的 harness、保存静态检查、契约和任务，然后进入修复循环；`resume()` 只继续同一个任务。每次 Run 保存 harness 快照、输入、构建日志、执行日志、观察结果和验证报告。没有独立预期时结果只能是 `observed`，不能称为行为验证通过。

当前 Rehost 后端限定为 Android ARM64 + Java/Maven/unidbg。它不承诺支持其他架构或其他执行后端。

## 目录职责

- `src/lightre/`：服务入口、API、workspace 读取和依赖准备。
- `src/agents/`：Codex session/run、SQLite 状态和后台 worker。
- `src/analyzers/`：Ghidra 导出和函数目录发布。
- `src/recovery/`：候选、源码、Knowledge、队列和批次恢复。
- `src/rehost/`：harness、契约、运行、重放和修复循环。
- `src/webui/`：函数浏览页面及其 HTTP 路由。
- `samples/minilua/`：样本源码和样本自身的构建脚本。

旧的根目录 `scripts/`、`src/execution/`、`src/lightre/cli/`、多命令入口和预制 examples 已从产品结构中移除。

## 检查

```bash
uv run --locked python -B -m unittest discover -s tests -p 'test_*.py'
```

默认测试使用临时 workspace 和外部工具替身，不启动真实模型或完整 Ghidra 分析。`tests/fixtures/execution/` 保存 Rehost 测试仍在使用的固定用例、checker 和适配说明。V0.3 真实目标记录位于 [交付记录](milestones/v0.3/DELIVERY.md)。

干净容器检查需要 Docker，会在新 Ubuntu 容器中下载依赖并执行真实 Ghidra 导出，不调用模型；它单独运行，不进入默认 unittest：

```bash
sh tests/test_clean_container.sh
```

真实 Codex 的内部 Skill 检查见下节，wheel／源码包检查见 [Prompt 资源发布](#prompt-资源发布)。

## Recovery 内部 Skill

Recovery 运行时仅维护 4 份指令文档：`RECOVER.md` 处理首轮/指定函数重跑，`FEEDBACK.md` 处理用户意见；两者注入同一个 `lightre-recovery/SKILL.md`，机器取证细节集中在其 `references/ASSEMBLY.md`。Codex CLI 读取所需方法，无需安装 Skill 或修改全局配置。首轮主动改善命名、类型、表达式与控制流，证据不足时保留低层结构并说明原因。

每批完整输入保存在 SO 分析目录的 `tasks/<id>/batch-NNNN.prompt.md`。方法正文及引用资料保存在同批 `.skills/` 目录，`.skills.json` 记录入口、描述和逐文件 SHA256；实际读取情况可结合 `.events.jsonl` 查看。目标和评论只记录于 `state.json`，不再另生成 `task.md`；完整调用映射见 [Recovery Prompt 与 Skill](design/RECOVERY_PROMPTS.md)。

源码位于 `source/src/*.c`，每个模块最多 20 个函数，按 Ghidra 分组组织；分组不代表原始源码模块。保留 `@lightre.begin/end` 中的 ID 和 origins，Web 隐藏这些机器定位及生产者注释。模块可以编辑或移动，`source_index` 是自动重建的派生索引。共享类型、接口与构建方式仍需验证，不再另生成 `source/README.md`。

每批 `.changes.json` 记录实际源码差异和改动状态，任务进度区分代码改进与保留；空白和注释变动不计作代码改进。函数列表显示当前源码名称，原始名称仍可搜索。函数改名后自动同步能唯一绑定的直接调用；函数指针、宏、头文件原型和无法解析的引用仍需核对，结果保存在任务目录 `renames.json`。

常规验证不调用模型；可显式运行真实 CLI 测试，它会调用已配置并登录的 Codex，在临时 workspace 中验证首轮命名/表达式改进（编译并检查边界输入）、汇编恢复、定向核对和评论处理：

```bash
uv run --locked python -B -m unittest discover -s tests -p test_recovery_skills.py
LIGHTRE_TEST_LIVE_CODEX=1 uv run --locked python -B -m unittest discover -s tests -p test_recovery_skills.py
```

## Prompt 资源发布

Codex CLI 使用的 Prompt、Skill 和 Workflow 指令文档由各模块维护，打包规则自动递归收集 `src/` 各包内的 `.md`，保留目录结构。模板使用 Jinja2：`{{NAME}}` 填入任务数据，`{% if ... %}`、`{% for ... %}` 管理条件与列表；缺少变量或语法错误会明确报错。指令正文、默认要求和生成文档中的说明都写在模板中。新增模板无需逐个登记文件。

构建 wheel 和源码包，并核对所有模块 Markdown 的路径与内容：

```bash
uv build --out-dir dist/prompt-release
LIGHTRE_TEST_DIST=dist/prompt-release uv run --locked python -B -m unittest discover -s tests -p test_packaging.py
```

发布目录中应只保留当前版本的一套产物。检查会同时发现遗漏的 Markdown 和 wheel 中残留的旧模板，并用 uv 将 wheel 安装到带中文、空格和引号的全新虚拟环境，在源码目录之外以隔离 Python 验证所有已安装 Markdown 可读、两个 Recovery Prompt 能渲染，以及 Skill 快照的绝对路径和相对引用有效；不调用模型或分析器。完整管理约定见 [Prompt 与 Markdown 资源](design/PROMPTS.md)。
