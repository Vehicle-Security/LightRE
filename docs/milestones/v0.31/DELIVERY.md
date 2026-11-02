# V0.31 开发交付与验证记录

状态：等待用户人工验收。本文仅记录已执行的检查、适用范围和历史故障；当前命令与契约统一见 [功能说明](../../USAGE.md)，最终验收范围见 [PLAN](PLAN.md)。V0.2 已单独验收，本版结果不表示候选差分或完整工程恢复已经完成。

## 当前交付基线

2026-11-02 增加 rehost 从零生成入口与代码/Markdown workflow，移除 examples 中的预制 Java harness 和按目标复制脚本。新 start 只提供公共依赖和说明，入口/ABI/JNI/系统环境由 Agent 调查并迭代实现；复用执行器的真实运行、记录、重放和完整失败交接。新流程说明见 [REHOST](../../USAGE.md#rehost-从零生成)。以下 2026-11-01 真实目标记录来自原实现阶段，不作为本次从零生成的集成通过依据。本次新链路只完成临时文件/子进程替身检查，未调用真实模型生成或真实 unidbg 运行。

2026-11-01 的通用化实现已提交为 `186876c`：核心不按目标名称分流，harness.json 显式关联 Java 入口、固定用例、独立检查器与产物。示例规则在 examples，minilua C 适配在原 samples/minilua；原 workspace 的关联已迁移，Java 工程和用例保留。

该基线全量轻量测试 151 项通过（18.668 秒），其中执行器 27 项，覆盖陌生目标接入、自定义入口/参数、多产物、无二进制输入、快照重放、篡改拒绝、失败续做和取消/恢复。测试使用临时 SQLite、真实文件和 Python 子进程，对 Java/Maven/模型使用外部边界替身，不冒充真实目标运行。

2026-11-02 本次全量轻量测试 156 项通过（24.048 秒），其中新增 rehost 5 项，覆盖空工程生成、环境错误反馈、完整返回交接、新 start 与同任务 resume、固定预期完成、无预期观察及重放/输入保护。使用临时文件与真实 Python 子进程替身，不运行真实模型或 unidbg；不能沿用旧模板时期的集成结果声明从零生成已完成真实验证。

## 真实目标及限制

| 目标 | 外部 SO 分析目录 | 已核对行为 |
| --- | --- | --- |
| DG d65f | /work2/dC6BDAE-analysis/dC6BDAE-d65f | JNI_OnLoad 七条注册、实例 init/ss/close、核心 0x1c07c 自然命中与实际 byte[] 返回 |
| minilua small | /work2/minilua-analysis/minilua-android-3974 | Android SO 薄 C 接口、完整输出/错误、Proto 与 luaV_execute 观察 |
| minilua large | /work2/minilua-analysis/minilua-android-1ae1 | 同八例；本次构建 luaV_execute RVA 0x839c，大小 0x278e0，40,504 条 ARM64 指令 |
| 开发 smoke | /work2/native-smoke-analysis/native-smoke-87a5 | 已知整数及模拟输入/输出缓冲区，边界和容量错误 |

DG SO SHA256 为 `d65fc4e408a87ab24c0f355096ceb8c406176379067657f409d6b51e24283472`，四例核心计数分别为 14851/14907/14918/14903，输出 1196/1178/1178/1175 字节。入口 hook 观察自然运行，没有绕过初始化、patch ready 或宣称 VM 语义已恢复。

DG 使用明确的 headless-v1 fixture：无真实签名或设备 attestation，时钟与随机种子固定，opaque runtime 回调含显式替身值，部分无返回值回调只收集观察。未知业务副作用未恢复，完整环境约定与 JNI 经验在 [DG 说明](../../../examples/dg/ADAPTATION.md)。通过仅适用于这些输入与环境，不能称为真实 GMS/设备行为。

minilua 不创建 Dalvik VM，不使用 JNI。原 src/minilua.c、Linux 构建及样本保留，Android 适配和工具链说明在 [源码项目](../../../samples/minilua/ANDROID.md)；Java ABI 与检查约定在 [minilua 说明](../../../examples/minilua/ADAPTATION.md)。语法错误没有可执行 Proto，其他例要求真实 VM 命中。输入及每类输出有 1 MiB 限制，溢出/ABI 错误明确失败。

## 通用化后的回归

以下套件与重放均通过。Run 保存实际 checker.py、contract.json、Java 工程、说明、输入、依赖及产物哈希；各路径下 verification.json 是结论依据。

| 目标 | 用例 | 套件目录 | 重放 Run |
| --- | --- | --- | --- |
| DG | 4/4 | /work2/dC6BDAE-analysis/dC6BDAE-d65f/runs/suite-20261101T132536-b73d6050 | /work2/dC6BDAE-analysis/dC6BDAE-d65f/runs/20261101T132546-ebc80b83 |
| minilua small | 8/8 | /work2/minilua-analysis/minilua-android-3974/runs/suite-20261101T132537-29bed1d8 | /work2/minilua-analysis/minilua-android-3974/runs/20261101T132558-1e4b9d92 |
| minilua large | 8/8 | /work2/minilua-analysis/minilua-android-1ae1/runs/suite-20261101T132720-f1b67aeb | /work2/minilua-analysis/minilua-android-1ae1/runs/20261101T132814-ea117576 |
| smoke | 4/4 | /work2/native-smoke-analysis/native-smoke-87a5/runs/suite-20261101T132538-7d5cd564 | /work2/native-smoke-analysis/native-smoke-87a5/runs/20261101T132545-44afafb7 |

DG 的 output.bin 与 minilua 的 proto.txt 在重放时哈希一致。Lua 八例是 smoke、tables、closures、errors、allocation、capabilities、syntax_error、runtime_error，核对完整 stdout/stderr、Lua 状态及进程约定。

## 初次目标适配记录

以下是通用化之前的历史验证，不作为旧执行契约仍受支持的承诺：

| 目标 | 历史 suite / verification.json | 历史重放 / verification.json |
| --- | --- | --- |
| DG | /work2/dC6BDAE-analysis/dC6BDAE-d65f/runs/suite-20261101T111501-649e0637 | /work2/dC6BDAE-analysis/dC6BDAE-d65f/runs/20261101T111535-b6d70ef6 |
| minilua small | /work2/minilua-analysis/minilua-android-3974/runs/suite-20261101T111358-d2474895 | /work2/minilua-analysis/minilua-android-3974/runs/20261101T111537-cba186c3 |
| minilua large | /work2/minilua-analysis/minilua-android-1ae1/runs/suite-20261101T111040-d0488f04 | /work2/minilua-analysis/minilua-android-1ae1/runs/20261101T111335-63ef9718 |

初次适配后轻量回归为 144 项。曾核对两套 Linux minilua manifest 各 28 个原输入及保留产物，原分析 binary SHA 为 `0432af2744d5f5fcc49d1acbb6c5ad0ff04d7cb62455f0ec2606e3420601e050`；该核对描述当时状态，后续文档修改不重写历史 manifest。

## 真实 Codex 修复与失败交接

smoke SO SHA256 为 `87a5cc0c837184ce97da14df55763f6bb940821bc511d01c3e74670e0b0dd468`。首次构建环境为 Linux aarch64、OpenJDK 25.0.4.1、Maven 3.8.7、Clang/LLD 18，SO 为 freestanding ARM64 Android ET_DYN。固定源码、构建命令及哈希在 /work2/native-smoke-analysis/fixtures/native-smoke/build.json。

在外部 harness 故意将 arithmetic 参数改为 (b,a)，外层捕获预期 24、观察 -16。任务 `harness-20261101T074737-ff5b2d89` 的真实 Codex 首轮约 40.993 秒，修正参数顺序，最终回复 596 字节，外层四例通过：

| 用例 | 整数 | 长度 | 输出 hex |
| --- | ---: | ---: | --- |
| 普通字节 | 24 | 5 | 5a5a23dda1 |
| 不同参数与输入 | -57 | 11 | 969a6c6d6d23736a746b6c |
| 整数边界、空输入 | -8589934591 | 0 | 空 |
| 容量不足 | 8589934589 | -1 | 空 |

任务位于 smoke SO 的 tasks/，证据为 round-0001/verification.json；实际 Run 为 `20261101T074820-98d8c5cd`、`20261101T074821-86fb8b32`、`20261101T074823-50bd8d13`、`20261101T074824-5c4fe8da`。新进程恢复后重新运行四例，在 `check-20261101T074936-b238189b` 记录通过，round 仍为 1，没有重复调用模型。

统一容器配置后的独立任务 `harness-20261101T075941-50026084` 也用真实 Codex 一轮修复并通过，命令明确使用 danger-full-access。此前 `harness-20261101T074258-8a20b455` 因 workspace-write 的 namespace 权限错误停止，后在容器权限下恢复；这只是历史环境故障，不是当前默认设置。

说明整合任务 `harness-20261101T102211-cf4eb995` 验证了修复前读取/更新 HARNESS.md 及外部证据区维护，汇总为 /work2/native-smoke-analysis/guide-integration-checks.json。文件输入、错误导出及历史快照的早期验证汇总在 /work2/native-smoke-analysis/integration-checks.json。

这些真实模型修复发生在开发 smoke 上。DG/minilua 由交互 Agent 按调查结果适配，本记录不宣称二者的模型自动修复效果已验证。

## 已知限制

旧实现没有完整检查器快照的 Run 保留只读，当前执行器拒绝直接重放；不可凭早期无 HARNESS.md 的兼容试验推断旧契约兼容。新格式 Run 可以缺少历史说明，但契约和其他内容依据必须完整。SO 与 Maven 依赖仍须在记录的路径，不保证跨机器离线重放。

已验证的是单边执行、固定预期和运行记录。没有交付恢复候选构建、双边差分、业务等价、VM 去虚拟化或独立源码工程。待用户按 PLAN 继续人工验收，开发测试不推进验收状态。
