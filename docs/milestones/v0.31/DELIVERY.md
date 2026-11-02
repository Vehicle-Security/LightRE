# V0.31 开发交付记录：最小 unidbg harness 与任务循环

状态：2026-11-01 已实现本版开发范围并完成下述真实集成检查，等待用户人工验收。V0.2 已单独验收；本记录不推进 V0.3 的真实目标适配或完整差分闭环。范围见 [PLAN.md](PLAN.md)，使用与恢复见 [USAGE.md](USAGE.md)。

## 执行核心通用化修正

按用户反馈，删除核心中的样本类型分流，改为显式入口、用例、独立检查器与产物列表。DG/minilua 的参数、JNI/Proto 检查、说明及便捷配置移到 examples；开发测试库的 prepare 也移到 examples/native-smoke/prepare.py。修复循环改用关联目标的固定契约。协议、命令及历史 Run 限制见 [执行契约](../../HARNESS_CONTRACT.md)。通用化后全量轻量测试 151 项通过（18.668 秒），其中执行器 27 项；真实 DG 4/4、minilua small/large 各 8/8、开发 smoke 4/4 及各自重放通过，记录见 [目标 harness](../../TARGET_HARNESSES.md#通用化后的回归)。下文为先前实现的历史记录。

## 后续授权：DG 与 minilua 真实运行

本次在原 workspace 配置两套目标 harness，DG 使用真实注册的实例 JNI 生命周期，minilua 在原 samples/minilua 源码项目构建独立 Android SO。运行契约、生成说明、实际用例/重放结果与限制统一记录在 [目标 harness](../../TARGET_HARNESSES.md)。这不等于 V0.3 差分闭环完成，也不改变尚待用户人工验收状态。

下文保留开发过程的历史范围与检查，不以历史“未适配”描述覆盖本次结果。

## 2026-11-01 每个 harness 的 HARNESS.md（初次实现记录）

新增 `src/execution/guide.py` 与 `prompts/HARNESS.md`，生成工程根目录的专用操作说明。
`prepare` 先生成说明再补齐 Java 工程，`run/repair` 为旧工程补齐缺失说明；已有适配正文保留。
修复 prompt 要求每轮先读取说明，先更新计划和依据，再修代码并同步接口、运行方法及缺口。
当前说明的最近检查区由外部执行器更新并引用真实报告，执行前说明参与 Run 快照和哈希核对。
历史 Run 不补写；旧无说明记录仍可重放，重放不改当前工程说明。

`guide <harness工程目录> --kind native-smoke|dg|minilua` 可独立生成说明。
已在 DG 与 minilua 原 workspace 的 `harnesses/dg/HARNESS.md`、`harnesses/minilua/HARNESS.md`
生成各自约定：DG 调查 JNI，minilua 纯 C 接口适配归原 `samples/minilua/` 源码项目。
两份说明明确待实现、待验证，没有生成可运行目标工程或宣称目标通过。
`harness.json` 的 `kind` 显式声明非 native-smoke 时，当前运行和修复入口拒绝套用固定自测预期。

轻量回归 137 项通过，约 14.065 秒。新增覆盖不同目标说明、已有正文保留、说明随修复进入快照、
最近检查引用与内容依据、当前说明改动后的旧副本重放、旧无说明 Run 兼容、历史说明篡改/
symlink 拒绝，以及非自测目标被固定检查器拒绝。

真实集成任务 `harness-20261101T102211-cf4eb995`：先故意将 arithmetic 参数改为 `(b, a)`，
预检观察 -16（预期 24）。真实 Codex 首条工具命令为 `cat HARNESS.md`，先写入适配计划，
再修正 Java 并同步说明；约 107.329 秒、一轮修复。外层重新构建并调用四例全部通过，
当前说明检查区指向 `round-0001/verification.json`，任务绑定包含说明的最终文件内容依据。
四个 Run 为 `20261101T102400-394f4ff9`、`20261101T102401-135e53eb`、
`20261101T102403-54ef6f13`、`20261101T102404-b73b38ad`，均含执行前的说明副本。

新 Run `20261101T102430-7ccdcdd2` 从上述首例重放通过，说明副本逐字节一致；
另用真实旧记录 `20261101T080052-520fc4de` 重放，新 Run `20261101T102431-de318882`
通过，旧/新快照均未补写说明。两次重放都没有改动当前说明，全部 protected 输入核对一致。
完整汇总：`/work2/native-smoke-analysis/guide-integration-checks.json`。
本次只运行开发自测库，没有运行 DG/minilua，也未提交或推送。

## 2026-11-01 统一容器安装与默认执行配置

用户明确工具现在及以后均在容器中运行，允许安装所需依赖，要求统一写入 `install_deps`。已将 JDK、Maven、Clang、LLD、Python venv 支持并入 `scripts/install_deps.sh`，移除开发阶段的独立 runtime 安装脚本。默认安装包含静态分析及运行依赖，`--runtime-only` 单独准备运行环境；已有 Ghidra 复用，不重新构建。依赖检查包含 Maven、编译器及 JDK 版本，不因已有 Ghidra 就忽略缺少的运行工具。

Harness 修复默认与 V0.2 一致使用 `danger-full-access`，以受控容器为执行边界，无需嵌套 namespace 或手动设置环境变量。仍优先尊重 `LIGHTRE_HARNESS_SANDBOX`，其次沿用 `LIGHTRE_CODEX_SANDBOX`；固定输入与检查器的内容核对保持生效。

已真实运行统一安装脚本的默认模式及 `--runtime-only`，本机依赖齐全，均正确跳过安装。轻量回归 133 项通过，约 13.604 秒；其中新增安装检查验证缺少 Maven/Clang/LLD/javac 时不能提前退出、旧 Java 被拒绝，以及安装分支确实包含所需包。安装分支用私有 PATH 与 apt 边界替身验证，没有为测试改动真实系统工具。

随后明确清除两个 sandbox 覆盖变量，使用默认配置运行真实任务 `harness-20261101T075941-50026084`：预检捕获参数顺序错误，一轮 Codex 修复后，外层四个真实 unidbg 用例全部通过。执行记录明确使用 `danger-full-access`，任务为 complete，固定基准及最终 harness 内容依据均核对一致。原始回复、事件和检查位于该任务的 `round-0001/`。下文保留统一配置前的开发历史。

## 实现

- `examples/native-smoke/`：两个无外部依赖的 C 测试函数和普通 Java/Maven harness；unidbg 固定为 0.9.9，使用 Unicorn2、Android ARM64/API 23。真实调用已知导出，分配模拟内存，传入文件字节、指针与长度，读回返回值与输出缓冲区。
- `scripts/harness.py`：`prepare`、`run`、`replay`、`repair`、`cancel` 五个入口。统一 `scripts/install_deps.sh` 按需准备 Java/JDK、Maven、Clang 和 LLD；`--runtime-only` 可单独安装运行依赖，已有 Ghidra 保持复用。
- `examples/native-smoke/prepare.py`（由原 fixture.py 移出）：用 Android ARM64 Clang target 构建无 libc 的固定测试库，记录源码、工具链、命令、日志和 SO 哈希；复用现有 workspace 身份与原始文件存储。
- `src/execution/runner.py`：每次从小型源码/pom 快照构建并运行，保存请求和输入，绑定实际 SO 与运行依赖 SHA256，Python 独立核对已知结果；旧 Run 重放不使用当前可编辑源码。
- `src/execution/loop.py`：先检查当前工程，再按需独立调用 `codex exec --ephemeral`。每轮保留完整 prompt、最终回复、事件、stderr、进程记录及外部检查。固定 SO、测试源码、关联、用例和检查器按内容依据核对；失败自动携带原始报告路径续做，取消或达到调用限制时保存停止原因，恢复时重新核对当前工程。
- `src/execution/process.py` 与 `storage.py`：进程超时、取消、进程组清理、原子记录及单写入者锁。SO 复用 V0.2 的写锁，共享 harness 另有锁；V0.2 队列忽略独立 harness 任务，不调度为候选批次。

没有新增 Web 页面、任务服务、harness 注册库、多后端抽象或通用 ABI 配置系统。固定预期是本测试库的外部基准，不是恢复候选的语义验证。

## 实际环境和样本

本次在 Linux aarch64、OpenJDK 25.0.4.1、Maven 3.8.7、Clang/LLD 18 环境实跑。测试库通过 `--target=aarch64-linux-android23 -shared -nostdlib` 构建；`readelf` 确认为 AArch64、ELF64、little-endian、DYN，无 libc 动态依赖。

- 外部 workspace：`/work2/native-smoke-analysis`
- SO 分析目录：`/work2/native-smoke-analysis/native-smoke-87a5`
- 测试 SO SHA256：`87a5cc0c837184ce97da14df55763f6bb940821bc511d01c3e74670e0b0dd468`
- 可编辑工程：`/work2/native-smoke-analysis/harnesses/native-smoke`
- 测试库构建依据：`/work2/native-smoke-analysis/fixtures/native-smoke/build.json`

首次 Run `20261101T073852-eed67318`：Maven 首次下载与构建约 195.006 秒；Java/unidbg 执行约 0.210 秒。观察到整数 `24`、长度 `5`、字节 `5a5a23dda1`，与 Python 预期一致。后续复用 Maven 缓存，仍在各 Run 的快照目录重新构建；不复用模拟器状态。

## 真实 Codex 修复与外部核验

在外部工程中故意将算术调用参数从 `(a, b)` 改为 `(b, a)`。外层实际运行捕获首例错误：预期 `24`，观察 `-16`。原始测试 SO、用例和 Python 检查器保持固定。

当前实现对应任务：`harness-20261101T074737-ff5b2d89`。首轮真实 Codex 约 40.993 秒，修正这一处参数顺序。最终 `result.md` 为完整 Markdown，596 字节；同目录保存实际命令、退出码、JSONL 事件与 stderr。外层在模型进程及其子进程结束后重新构建、调用固定 SO，四例均通过：

| 用例 | 整数结果 | 有效输出长度 | 输出 hex |
| --- | ---: | ---: | --- |
| 普通字节 | 24 | 5 | `5a5a23dda1` |
| 不同整数、密钥及输入 | -57 | 11 | `969a6c6d6d23736a746b6c` |
| 有符号整数边界、空输入 | -8589934591 | 0 | 空 |
| 输出容量不足 | 8589934589 | -1 | 空 |

证据位于该任务的 `round-0001/verification.json`，对应真实 Run：`20261101T074820-98d8c5cd`、`20261101T074821-86fb8b32`、`20261101T074823-50bd8d13`、`20261101T074824-5c4fe8da`。核对 `protected.json` 中全部内容依据仍一致，完成状态绑定通过检查的 harness 文件哈希。

随后用新 Python 进程恢复同一任务，重新实际运行四例，通过后保持 round=1，没有再次调用模型。新检查为 `check-20261101T074936-b238189b/verification.json`，证明没有沿用旧通过结论直接退出。

此前任务 `harness-20261101T074258-8a20b455` 的前三轮在默认 `workspace-write` 下遇到 `bwrap: No permissions to create a new namespace`，模型未能执行工具，外部仍观察到错误；达到调用方 3 轮上限后保留 `limit`。未把正常 CLI 退出码视为成功。随后在当前受控容器显式指定 `LIGHTRE_HARNESS_SANDBOX=danger-full-access` 恢复该任务，第 4 轮修复并通过。这个历史任务用于记录实际环境限制和失败交接；上面的新任务验证了补齐固定基准检查后的当前实现。这是统一容器配置前的历史记录；后续按用户明确的容器运行要求，将默认模式统一为 `danger-full-access`，不再要求手动覆盖。

## 重放、文件输入与真实失败路径

外部汇总：`/work2/native-smoke-analysis/integration-checks.json`。

- 再次把可编辑工程的算术参数改错，从最初正确 Run 的保存副本重放；新 Run `20261101T074627-b810734a` 仍通过。随后恢复可编辑工程。
- 将当前工程的缓冲区导出名临时改为不存在的符号；Run `20261101T074629-b7e690de` 为 `execution_failed`，日志明确记录 `Missing export: missing_export_for_test`，没有成功观察。随后恢复工程。
- 使用真实输入文件，Run `20261101T074630-e4f21f66` 返回整数 `-57`、长度 `13`、字节 `98966c642f6a6a7573730889f5`；原始文件路径、哈希与内容快照已保存。
- 删除该原始输入文件后，新 Run `20261101T074632-a02e2bba` 从保存副本重放通过；直接再次引用已删除文件的 Run `20261101T074633-ed71631c` 明确为 `error` 并保留原因。

旧 Run 仍按原路径和哈希引用 SO 与 Maven 依赖，不保证资源丢失时离线重放；内容改变时明确拒绝。这里只验证固定测试库，不能用这些记录证明 DG、Lua 解析器或虚拟机已经运行。

## 轻量回归与人工验收边界

`tools/venv/bin/python -m unittest discover -s tests`：129 项通过，约 12.125 秒，其中 9 项为新增 execution 检查；`git diff --check` 和当时安装脚本 shell 语法检查通过；统一安装入口后的回归另见下方更新记录。

新增检查使用临时 SQLite、真实快照/日志/任务文件、真实 Python 子进程，将 Java/Maven/模型作为外部边界替身，覆盖：

- 文件输入、修改可编辑工程后的快照重放、SO/请求/依赖替换拒绝。
- 构建失败、缺失导出、文件缺失、结果不匹配。
- 模型声称完成但检查失败时继续，下一轮读取完整上一轮记录；原文不被摘要覆盖。
- 最终回复缺失、CLI 异常、调用上限、已完成任务恢复后重新检查当前内容。
- 在途 CLI 取消、进程退出记录、同任务继续、共享写锁互斥。
- 模型尝试改动固定契约时，外部检查拒绝发布通过结论。

这些轻量检查不替代前面的真实 unidbg 和 Codex 运行。尚待用户按 [PLAN](PLAN.md) 人工验收；DG JNI、minilua Android SO、候选构建、双边差分、VM 语义恢复及完整工程 Goal Loop 仍未交付。本次未重启或修改 DG/minilua 的分析和 Web，也未提交到 GitHub。
