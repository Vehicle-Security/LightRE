# DG 与 minilua：真实目标 harness

2026-11-01 按用户后续授权配置，使用两个目标原来的 workspace。已实现单边运行、输入/源码快照、外部核验和新进程重放；尚未实现候选构建、双边差分、VM 业务语义恢复；修复循环已改为消费目标契约，但尚未实测真实目标的自动修复。V0.31 仍待用户人工验收。

## 专用说明先于工程

每个可编辑工程根目录维护一份 `HARNESS.md`，类似随工程保存的专用 skill。样本脚本 `examples/configure.py` 先生成说明，再复制缺少的 pom/Java 文件；已有说明和代码不覆盖。Agent 每次先读说明、关联、用例与实际日志，更新适配计划后再改代码。新 SO 版本仍要核对 ABI、初始化和资源。

`guide <工程目录> --target <名称> --instructions examples/<样本>/ADAPTATION.md` 可只生成说明。Markdown 生成和 SO 关联均不代表运行通过。外部执行器维护说明的最近检查区，引用实际 `verification.json`；Run 保存执行前的说明、源码和 pom，历史说明不补写。

| 目标 | 可编辑说明 | SO 分析目录 |
| --- | --- | --- |
| DG | `/work2/dC6BDAE-analysis/harnesses/dg/HARNESS.md` | `/work2/dC6BDAE-analysis/dC6BDAE-d65f` |
| minilua small | `/work2/minilua-analysis/harnesses/minilua/HARNESS.md` | `/work2/minilua-analysis/minilua-android-3974` |
| minilua large | 同一个 minilua 工程 | `/work2/minilua-analysis/minilua-android-1ae1` |

每个 SO 用自己的 `harness.json`、`harness-cases.json` 和 `runs/`。仓库 `examples/dg/harness/` 与 `examples/minilua/harness/` 提供可复制模板；外部工作区是当前任务唯一可编辑的 Java 工程。native-smoke 只用于开发自测。

## 配置与运行

统一依赖入口，Ubuntu 24 Linux 容器，JDK 17+、Maven、Unicorn2、unidbg 0.9.9、Android ARM64 / API23：

```bash
cd /work2/LightRE
bash scripts/install_deps.sh --runtime-only
# Android minilua 构建另外需要 LLVM14、Clang/LLD 和官方 NDK r27d Bionic sysroot：
bash scripts/install_deps.sh --android
```

`--android` 仍是同一安装脚本的选项。ARM64 宿主使用原生 Clang 与 NDK sysroot，不执行 NDK 附带的 x86_64 编译器。首次下载 NDK 和 Maven 依赖需要网络。

```bash
# DG：关联已有 SO 分析目录及其配套字节码。
tools/venv/bin/python examples/configure.py \
  /work2/dC6BDAE-analysis/dC6BDAE-d65f --kind dg \
  --programs /work2/dg-samples/dC6BDAE/bytecode/libdC6BDAE49562F.so

# minilua：在原源码项目构建，必须使用一个尚不存在的输出目录。
tools/venv/bin/python samples/minilua/scripts/build_android.py \
  --output /work2/minilua-analysis/fixtures/android-new-small --obfuscation small
# 可选 none 或 large；large 对 luaV_execute split 后 flatten 两轮。
tools/venv/bin/python examples/configure.py \
  /work2/minilua-analysis/fixtures/android-new-small/libminilua.so \
  --kind minilua --workspace /work2/minilua-analysis
```

minilua 的 `configure` 打印导入的 SO 子目录。后续对该目录操作：

```bash
tools/venv/bin/python scripts/harness.py cases /work2/minilua-analysis/minilua-android-3974
tools/venv/bin/python scripts/harness.py run /work2/minilua-analysis/minilua-android-3974 --case closures
tools/venv/bin/python scripts/harness.py check /work2/minilua-analysis/minilua-android-3974
tools/venv/bin/python scripts/harness.py check /work2/dC6BDAE-analysis/dC6BDAE-d65f
tools/venv/bin/python scripts/harness.py replay <SO目录> <旧Run目录>
```

`run --request request.json` 也支持自定义请求，但输入内容 SHA256 和参数必须匹配已配置的预期；没有基准时明确失败，不借用 smoke 预期。`check` 串行运行固定用例，保存独立 suite 报告，持有 SO 与共享工程写锁，核对整套检查期间源码和契约未改变。

执行器与样本规则已经拆分，通用入口和检查器协议见 [执行契约](HARNESS_CONTRACT.md)。原 workspace 的关联已迁移为显式 checker/artifacts 配置，已有工程与用例保留。

Run 保存 `checker.py`、`input.bin`、`request.json`、`expected.json`、`contract.json`、`harness/`、`observation.json`、命令/版本/退出码/stdout/stderr、`manifest.json` 和 `verification.json`。DG 另存 `output.bin`，minilua 另存 `proto.txt`。重放使用旧副本并校验产物哈希；源码、说明、请求、契约、SO、依赖或旧产物被改动时拒绝通过。仍需要原 SO 与 Maven 依赖保留在记录中的路径，不保证跨机器离线重放。

`repair` 已改为读取任意目标显式配置的固定契约，不再按样本类型限制；本轮没有实际调用模型修复 DG/minilua，不能据此宣称二者自动修复已验证。

## DG 的执行契约与限制

固定 SO SHA256 为 `d65fc4e408a87ab24c0f355096ceb8c406176379067657f409d6b51e24283472`。实际执行 JNI_OnLoad，动态观察七条 RegisterNatives；此版必须调用 DroidGuard **实例方法**。生命周期为 initNative → ssNative → closeNative，通过本版 RVA `0x1c07c` 的 hook 记录自然执行的 VM 命中，另记 init/ss 分阶段计数。未改写 native ready 标志或跳过初始化。其他版本必须先调查并提供 `--vm-entry-rva`。

实际参数：flow=attest、callback=null、空 Bundle、尾部 int=0/0，ss 的键为 dg_email、dg_package、dg_androidId。要求有效正 handle、七条正确注册、init/ss 两阶段核心命中、非空实际返回数组、close 完成，无未知 JNI 或模拟错误。unidbg 吞掉异常后返回值的情况也扫描执行日志判失败。

使用明确的 `environment_profile=headless-v1` 测试环境：

- GMS fixture 包 versionCode=0，flags=0、Java uid=10000，无真实 APK 签名及声明权限；其他查询包视为未安装。
- 无传感器、显示器、输入设备、启用的 accessibility 服务、binder 服务或 persistent data block。总内存 512MiB、可用 256MiB、threshold 32MiB，feature registry 仅 ram.low。
- 空 settings/preferences，缺键依 Android API 返回 null、调用者 default 或 SettingNotFoundException；稀疏 Java 属性和 unknown 设备 Build 字段。
- 空内存 JKS store、真实 SUN provider；无 AndroidKeyStore key generator，以 NoSuchProviderException 交给目标处理。Java Socket 为离线环境，以 NoRouteToHostException 返回，不伪造硬件证明或签名证书。
- Java 时钟及随机种子从请求保存。缺少本版 DEX，`gr/i/j/h/m/k` 的设备语义未知，分别使用显式请求值 `0/0/0/空串/空串/空串`。`g(float[])` 与 `n()` 只收集调用，保存 float 原始 bits，不实现未知 Java 副作用。

这些替身是已声明的测试输入和环境假设。通过说明 SO 在此环境和四份字节码上可运行，不代表真实 GMS/设备 attestation 行为或 VM 业务语义已还原。其他 JNI 未支持时明确失败，不通用返回零。

## minilua 的源码归属与 ABI

适配仅新增到原源码项目：[`unidbg_api.c`](../samples/minilua/src/unidbg_api.c) 和 [`build_android.py`](../samples/minilua/scripts/build_android.py)。原 `src/minilua.c`、Linux 构建脚本和交付物保留。构建目录保存源码副本、命令、工具版本、输入/产物哈希、混淆器 pin/seed/pass 日志和 `build.json`。

Java 不创建 Dalvik VM，不使用 JNI。薄 C ABI 每次新建并关闭 Lua 状态，从内存编译执行仓库 `.lua`，支持 chunk_name 和一个可选 vararg。stdout/stderr、Lua 状态和完整 Proto 被真实读回；Proto 包含实际指令、常量、source、参数/栈/upvalue 字段和子函数关系，不保存不可重放的宿主指针。Java hook 实际 luaV_execute 地址记录命中。

返回 0 成功，语法错误 3、运行错误 2；负数表示 ABI/资源错误，不能按脚本预期错误放行。源码及每类输出上限 1MiB（输出包含终止符空间），溢出明确失败。串行 ABI，不支持并发共享一个 SO 的全局输出区。原 Lua 子集和库能力保持其源码定义，未加入任意 luac 二进制加载器。

## 真实结果（2026-11-01）

| 目标 | 固定用例 | 结果 | 完整检查记录 |
| --- | --- | --- | --- |
| DG d65f | 4 份配套 pia_attest .bin | 4/4 通过，VM 14851/14907/14918/14903 次，输出 1196/1178/1178/1175 字节 | `/work2/dC6BDAE-analysis/dC6BDAE-d65f/runs/suite-20261101T111501-649e0637/verification.json` |
| minilua small 3974 | 8 个仓库 Lua 用例 | 8/8 通过 | `/work2/minilua-analysis/minilua-android-3974/runs/suite-20261101T111358-d2474895/verification.json` |
| minilua large 1ae1 | 同 8 个用例 | 8/8 通过 | `/work2/minilua-analysis/minilua-android-1ae1/runs/suite-20261101T111040-d0488f04/verification.json` |

Lua 用例为 smoke、tables、closures、errors、allocation、capabilities、syntax_error、runtime_error，分别核对完整 stdout/stderr 与状态。语法错误没有可执行 Proto，VM 命中为 0 是正确路径；其他例要求实际执行入口。large 的 luaV_execute RVA 为 `0x839c`，符号大小 `0x278e0`，即 40504 条 ARM64 指令。

small/large 都完成 closures 新进程重放，Proto 哈希一致；DG 完成首个字节码的新进程重放，output.bin 字节哈希一致。各自 Run 由 manifest.replay_of 关联原记录。

重放记录：

- DG：`/work2/dC6BDAE-analysis/dC6BDAE-d65f/runs/20261101T111535-b6d70ef6/verification.json`。
- minilua small：`/work2/minilua-analysis/minilua-android-3974/runs/20261101T111537-cba186c3/verification.json`。
- minilua large：`/work2/minilua-analysis/minilua-android-1ae1/runs/20261101T111335-63ef9718/verification.json`。

完整轻量回归 **144 项通过**（16.229 秒）；其中执行器 20 项、安装器 4 项，使用临时文件和外部进程替身，真实 unidbg 结果单列如上。另核对两套 Linux manifest 的 28 个原输入及当前保留的产物哈希，均无变化；原分析工作区的 Linux binary SHA 仍为 `0432af2744d5f5fcc49d1acbb6c5ad0ff04d7cb62455f0ec2606e3420601e050`。shell 语法与 diff 空白检查通过。

## 通用化后的回归

执行核心已去掉目标名称分流。以下运行通过显式配置的独立检查器完成，检查器源码、用例和产物声明均进入 Run 快照；原 Java 工程及用例未替换。

| 目标 | 全套用例 | 新套件记录 | 新进程重放 |
| --- | --- | --- | --- |
| DG | 4/4 | `/work2/dC6BDAE-analysis/dC6BDAE-d65f/runs/suite-20261101T132536-b73d6050` | `/work2/dC6BDAE-analysis/dC6BDAE-d65f/runs/20261101T132546-ebc80b83` |
| minilua small | 8/8 | `/work2/minilua-analysis/minilua-android-3974/runs/suite-20261101T132537-29bed1d8` | `/work2/minilua-analysis/minilua-android-3974/runs/20261101T132558-1e4b9d92` |
| minilua large | 8/8 | `/work2/minilua-analysis/minilua-android-1ae1/runs/suite-20261101T132720-f1b67aeb` | `/work2/minilua-analysis/minilua-android-1ae1/runs/20261101T132814-ea117576` |
| 开发 smoke | 4/4 | `/work2/native-smoke-analysis/native-smoke-87a5/runs/suite-20261101T132538-7d5cd564` | `/work2/native-smoke-analysis/native-smoke-87a5/runs/20261101T132545-44afafb7` |

各行重放均通过，声明产物哈希一致。轻量回归扩展为 151 项，其中执行器 27 项；新增陌生目标接入、任意入口与多个产物、无二进制输入、独立检查器快照及篡改拒绝等检查。未调用真实模型执行 DG/minilua 自动修复。
