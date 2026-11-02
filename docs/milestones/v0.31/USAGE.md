# V0.31：native-smoke 运行、重放与 harness 修复

本版使用专用、无 JNI 的小型测试 SO 验证 unidbg 调用和 harness 修复。本文先说明原 native-smoke 开发范围。用户随后授权的 DG/minilua 运行适配、configure/cases/check 命令与实测见 [目标 harness](../../TARGET_HARNESSES.md)；恢复候选编译和双边差分仍在后续范围。代码与开发检查不代表用户人工验收通过。

本文的 `native-smoke-analysis` 是开发自测路径示例，不要求每个目标另建运行 workspace。`prepare --workspace` 也接受用户选定的已有 workspace 根目录，测试 SO 仍使用自己的 SO 子目录。后续 DG 在原 `/work2/dC6BDAE-analysis` 下维护需要 JNI 的 `harnesses/dg/`，minilua 在原 `/work2/minilua-analysis` 下维护纯 native 的 `harnesses/minilua/`；分别关联各自 SO 并保存 Run，不共用 native-smoke 工程。这两份目标 harness 已按后续授权实现，详见上方目标文档。

让 Agent 读取项目 SKILL 后，可以这样发起：

```text
读取 LightRE/SKILL.md，在 ./native-smoke-analysis 准备并运行 v0.31 测试 SO，检查整数和缓冲区用例，保存一次重放结果。
```

要修复现有工程：

```text
读取 LightRE/SKILL.md，使用 v0.31 harness 修复循环处理 ./native-smoke-analysis/native-smoke-<哈希>，先检查当前工程，失败时自动修复并检查固定用例。
```

以下命令供 Agent 和开发排查使用；路径以实际调用目录为准，传给脚本时使用绝对路径。运行入口无需 Ghidra，不会新建 V0.2 候选任务，也无需打开 Web。

## 环境与固定测试库

需要 Python 3.10+、Java/JDK 17+、Maven、Clang 和 LLD。Ubuntu 可按需准备：

```bash
bash /absolute/LightRE/scripts/install_deps.sh
```

统一安装入口同时准备静态分析与 native-smoke 依赖，已有 Ghidra 不会重装。仅准备本版运行环境可使用 `bash /absolute/LightRE/scripts/install_deps.sh --runtime-only`。运行环境以 Ubuntu 24 Linux 容器为基准。

示例 Maven 工程固定 unidbg 0.9.9，选择 Unicorn2 后端，模拟 Android ARM64 / API 23。工程中的编译插件、依赖插件及直接依赖版本也已固定。首次 Maven 下载依赖需要联网，后续复用本机 Maven 缓存；Run 不复制缓存，只保存实际运行依赖路径和 SHA256。源码编译与 Java/Maven 版本、日志及命令分别记录。

```bash
/absolute/LightRE/tools/venv/bin/python /absolute/LightRE/examples/native-smoke/prepare.py \
  --workspace /absolute/native-smoke-analysis
```

此命令打印本次选定的 SO 分析目录。准备操作使用 `examples/native-smoke/native_smoke.c`，以 `clang --target=aarch64-linux-android23 -fuse-ld=lld -shared -nostdlib` 构建专用测试库，不依赖 NDK 头文件或 libc。这个选择只适用于本例两个无外部依赖的函数，不是通用 Android SO 构建器，也不是恢复候选构建功能。

两条已知 ABI：

- `smoke_arithmetic(int32_t a, int32_t b) -> int64_t`：返回 `a * 3 - b`，先扩展到 64 位。
- `smoke_transform(const uint8_t *input, uint32_t length, uint8_t *output, uint32_t capacity, uint32_t key) -> int32_t`：逐字节输出 `input[i] XOR ((key + i) & 255)`，返回长度；容量不足返回 `-1`。

测试 SO 位于选定目录的 `binary/original.so`。可编辑 Java 工程放在外层 workspace 的 `harnesses/native-smoke/`，以当前 SO 的 `harness.json` 关联。重复准备保留已有可编辑 harness，不覆盖用户或模型的修改。`fixtures/native-smoke/build.json` 记录测试库构建来源，不能当作候选编译结果。

## 每个工程的专用说明

工程根目录的 `HARNESS.md` 类似专用 skill：每次先读，核对目标、入口 ABI、初始化、参数、构建运行方法和已知缺口，再适配代码。`prepare` 先生成说明再补齐 Java 工程；普通 `run` 和 `repair` 为旧工程补齐缺失说明，已有正文保留。模型每轮先更新适配计划和依据，修复后同步实际修改。外部执行器在检查结束后更新当前说明的最近检查区，引用真实报告；模型的文字声明不能代替检查。

也可以在创建目标工程之前单独生成说明：

```bash
/absolute/LightRE/tools/venv/bin/python /absolute/LightRE/scripts/harness.py guide \
  /work2/dC6BDAE-analysis/harnesses/dg --target dg \
  --instructions /absolute/LightRE/examples/dg/ADAPTATION.md
/absolute/LightRE/tools/venv/bin/python /absolute/LightRE/scripts/harness.py guide \
  /work2/minilua-analysis/harnesses/minilua --target minilua \
  --instructions /absolute/LightRE/examples/minilua/ADAPTATION.md
```

`guide` 只生成 Markdown，不创建 Java 工程、SO 关联或运行证据。DG 模板要求调查 JNI 类/签名与初始化；minilua 模板明确纯 C 调用，适配源码仍在 `samples/minilua/`。生成的说明列出模板实际接口和环境限制，所选目标初始仍没有外部通过记录。通用 `configure` 显式关联工程、main_class、用例、checker 与 artifacts，`run/check/replay/repair` 使用相同契约。DG/minilua 的模板复制改用 `examples/configure.py`。不再识别 `kind` 或默认回退 smoke；旧关联与缺少检查器快照的旧 Run 需按 [执行契约](../../HARNESS_CONTRACT.md) 处理。

## 单次运行

```bash
/absolute/LightRE/tools/venv/bin/python /absolute/LightRE/scripts/harness.py run \
  /absolute/native-smoke-analysis/native-smoke-<哈希>
```

默认用例：`a=7, b=-3, key=90, input_hex=00017f80ff`，预期整数 `24`、长度 `5`、输出 `5a5a23dda1`。Python 独立核对整数、有效输出长度及字节内容，不以 Java 正常退出或 SO 加载成功作为通过。

`--request /absolute/case.json` 可提供其他输入，例如：

```json
{
  "a": -11,
  "b": 24,
  "key": 254,
  "input_file": "seed.bin"
}
```

输入文件相对此 JSON 的目录解析，在 Run 中保存内容快照与原路径/哈希；也可使用 `input_hex`，二者必须且只能选择一个。`a,b` 为有符号 32 位整数，`key` 为 0–255，可选 `capacity`，默认等于输入长度。输入及输出容量最多 1 MiB。这是固定样本的少量参数，不是任意 ABI 调用配置语言。

Java 读取快照文件，在模拟器内分配输入/输出缓冲区、传递指针与长度、调用已知导出并读回输出。每次启动全新的 Java 进程和模拟器。`--timeout` 指定每个外部进程的时限，默认 300 秒；它分别适用于构建、运行和每轮 Codex，不是整个任务的总时限。

每次尝试独立保存：

```text
<SO>/runs/<run_id>/
  manifest.json          # 实际 SO、harness 文件、请求、输入和依赖的内容依据
  harness/               # 执行前 HARNESS.md、源码与 pom 副本；在此构建运行
  request.json
  input.bin
  expected.json          # Python 基准
  observation.json       # Java 读回的实际观察；失败可能不存在
  verification.json      # 外部核对结果；失败也保存
  versions/java/         # 版本、命令、退出码和 stdout/stderr
  versions/mvn/
  build/
  execute/
```

缺少文件、编译失败、缺失导出、超时、无结果、结果不符均返回非零退出码并保存失败记录；成功返回零。详情见该次 `verification.json` 及对应日志。

## 重放

```bash
/absolute/LightRE/tools/venv/bin/python /absolute/LightRE/scripts/harness.py replay \
  /absolute/native-smoke-analysis/native-smoke-<哈希> \
  /absolute/native-smoke-analysis/native-smoke-<哈希>/runs/<run_id>
```

从旧 Run 的源码、pom、请求及输入副本重新构建，在新进程执行并保存新 Run。可编辑工程即使已经修改，旧 Run 仍使用旧副本。旧副本、原始 SO 或解析到的运行依赖发生改变时明确拒绝重放，不静默采用新文件。SO 和 Maven 依赖仍按保留路径引用，移动或丢失这些资源需要先恢复，记录本身不保证跨机器离线运行。

有 `HARNESS.md` 的 Run 会核对并复制保存的说明，修改历史说明会被拒绝。早期没有说明的 Run 继续按其原有文件集合重放，不补写历史副本；重放也不更新当前可编辑工程的说明。

## 修复循环、取消和恢复

```bash
/absolute/LightRE/tools/venv/bin/python /absolute/LightRE/scripts/harness.py repair \
  /absolute/native-smoke-analysis/native-smoke-<哈希> --max-rounds 3
```

先真实检查四个固定用例：普通字节、不同参数和字节、整数边界及空缓冲区、容量不足。当前已经通过时保存检查依据并停止；否则通过本机已登录的 `codex exec --ephemeral` 修复可编辑 harness。模型不修改固定 SO、测试源码、预期或检查器。

每轮在 `tasks/harness-<id>/round-0001/` 保存实际 `prompt.md`、完整 `result.md`、`events.jsonl`、`stderr.log`、`execution.json` 和外部 `verification.json`。最终返回缺失时明确记录，不生成替代正文。外层等待相关子进程退出，再重新构建并检查当前文件；失败自动将上一轮完整正文和具体检查记录的路径交给下一轮，不提取摘要，也不无限拼接历史正文。

任务自身的 `task.md`、`contract.json` 和 `protected.json` 保存原始要求、固定 ABI/用例/预期及基准内容依据。`state.json` 保存轮次、最新报告引用和停止原因。单次调用默认最多 3 轮；达到调用方限制保留 `limit`，连续两次 CLI 异常或缺失最终回复保留错误及继续条件；缺资源或检查器异常不标完成。重新调用时先检查真实当前内容，旧通过结论不能替代检查。

```bash
# 取消请求由运行中的执行器读取，并终止在途进程组。
/absolute/LightRE/tools/venv/bin/python /absolute/LightRE/scripts/harness.py cancel \
  /absolute/native-smoke-analysis/native-smoke-<哈希>/tasks/harness-<id>

# 恢复同一任务；保留历史轮次，明确消费上次取消请求。
/absolute/LightRE/tools/venv/bin/python /absolute/LightRE/scripts/harness.py repair \
  /absolute/native-smoke-analysis/native-smoke-<哈希> \
  --task /absolute/native-smoke-analysis/native-smoke-<哈希>/tasks/harness-<id> --max-rounds 3
```

前台 `Ctrl-C` / SIGTERM 也会请求停止，并保存当前状态。不可捕获的进程终止可能留下未完成记录；锁仍被活进程占有时不能另起写入者，应先检查日志中的进程并结束旧进程，再恢复。

同一 SO 共享 V0.2 的写入锁，共享 harness 也有独立文件锁，忙碌时明确报错。harness 任务不会进入 V0.2 候选调度队列。没有新增 Web 页面或后台任务服务。

修复循环面向受控容器，默认使用 `danger-full-access`，以可编辑 harness 为工作目录；无需手动设置执行模式，也不需要容器支持嵌套 namespace。可通过 `LIGHTRE_CODEX_BIN` 指定 CLI，`LIGHTRE_CODEX_PROFILE` 复用本机 profile；显式覆盖模式时优先使用 `LIGHTRE_HARNESS_SANDBOX`，其次沿用 `LIGHTRE_CODEX_SANDBOX`。无论 CLI 是否正常返回，最终状态均以实际检查为准。
