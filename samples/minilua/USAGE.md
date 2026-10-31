# 构建与使用

该示例保留 LuaJIT 上游 minilua 的完整功能。大函数版用于复杂控制流测试，小函数版用于定位基础问题。两者各自有基线、混淆版和实测记录。`README.md` 保留第一阶段需求。

## 大函数版：优先用于复杂度测试

现成目标：`large/deliverable/minilua`。直接交给 Agent：

```text
读取 /work2/LightRE/SKILL.md，分析 /work2/LightRE/samples/minilua/large/deliverable/minilua，workspace 使用 /work2/minilua-analysis，然后打开结果供我查看。
```

|测试目的|文件|luaV_execute RVA|指令数|
|---|---|---|---:|
|复杂混淆、无普通符号|`large/deliverable/minilua`|`0x4d58`|40,589|
|核对名称、边界|`large/reference/obfuscated.full`|`0x4d58`|40,589|
|对照原始逻辑|`large/reference/baseline.full`|`0x4d58`|1,099|
|对照符号剥离影响|`large/reference/baseline.stripped`|`0x4d58`|1,099|

该函数有 162,356 字节，IR 基本块从 195 增至 2,489；实际机器 CFG 基本块数未测量。200 条一页时需要 203 页，适合检查长函数识别、分页、滚动与搜索。函数边界可能被分析器漏识别，完整参考为 `large/reference/obfuscated.luaV_execute.asm`，最终以它和 ELF 符号范围为准。

实现为额外给 `luaV_execute` 添加 `noinline`，仅选中它做基本块拆分（`split_num=5`）和两轮平坦化。第二轮将第一轮的分派控制流继续展开；大量状态读写、比较树与返回分派的分支保留到机器码。固定种子和 pass 参数记录在 `sources.json`、`large/manifest.json`。没有使用无意义指令填充。除该函数外，294 个函数的 IR 一致。

Clang `-Os`、llc `-O2` 和 16 KiB 最大链接页尺寸用于控制体积；基线和混淆版使用相同参数。程序仍为动态 PIE，交付目录只有二进制和演示脚本，总共 265,007 字节。当前引用 glibc 的实际版本见 `large/reports/elf.json`。源码补丁在 `patches/minilua-large.patch`；生成的完整源码在 `large/reference/minilua.c`，沿用现有可读排版。

```bash
cd /work2/LightRE/samples/minilua
bash scripts/build.sh --large          # 构建并运行全部必要验证
# 仅复测已有产物：
bash scripts/test.sh --large
python3 scripts/verify.py --large
python3 scripts/check_size.py large/deliverable
# 同环境再构建一次并核对五份文件的 SHA-256：
python3 scripts/check_rebuild.py --large
# 原生 AArch64 演示：
large/deliverable/minilua large/deliverable/smoke.lua
```

上述构建生成 `large/reference/`、`large/reports/`、`large/sources.json` 和 `large/manifest.json`，不替换小函数版。大函数版默认 `-Os`；如果手动修改拆分次数、轮数或优化级别，构建会重新检查不少于 40,000 条指令且交付总大小不超过 300,000 字节。逐次测试耗时保存在 `large/reports/tests.json`；本机表操作约 0.10 秒，大量分配约 0.9 秒，仅为该环境实测参考。

要核对 LightRE 已导出的结果，仍使用下文的 `check_lightre.py --workspace ...`。它会按 SHA-256 自动识别大小样本，写入对应 `reports/lightre.json`。不自动启动分析或 Web。实际完整功能验证见 `large/reports/verification.md`；此样本面向 V0.1 人工验收。

## 小函数版

以下命令与路径针对原来的小函数版。它只给 `luaH_get`、`luaH_set` 添加 `noinline`，并仅对这两个函数平坦化。

`src/minilua.original.c` 保留上游原始排版和 SHA-256；开发用的 `src/minilua.c` 使用 clang-format 14 格式化为两空格缩进、独立语句换行。规则保存在 `.clang-format`，`src/modifications.patch` 同时记录排版调整和两处 `noinline`。修改源码后，可运行 `python3 scripts/format_source.py` 重新格式化并更新补丁，再执行构建。

## 一键构建和测试

本次实测环境为 Ubuntu 24.04、原生 AArch64 Linux，使用 Clang/LLVM 14.0.6、GNU binutils 2.42、glibc 2.39。没有使用 QEMU。依赖安装脚本需要 root；已有依赖时直接构建：

```bash
cd /work2/LightRE/samples/minilua
# 需要准备依赖时执行：sudo bash scripts/install_deps.sh
bash scripts/build.sh
```

构建会校验原始源码 SHA-256 和源码补丁，检出固定混淆器 commit、应用函数白名单补丁并编译插件，然后生成两版程序，真实运行测试并检查 ELF、strip 来源、IR 修改范围、机器码分派结构和交付目录大小。任一步失败都会非零退出，只有全部通过才生成 `manifest.json`。

默认 `-O2`，预算不足时可显式使用 `bash scripts/build.sh --opt Os`。两版共用一次 Clang 优化产生的基线 bitcode，混淆版在此基础上运行 pass；两版都由相同的 `llc -O2` 后端生成机器码，使用相同链接参数。

单独复测已有产物：

```bash
bash scripts/test.sh
python3 scripts/verify.py
python3 scripts/check_size.py
# 检查当前成功构建能否在同环境重复生成相同字节（会再构建一次）：
python3 scripts/check_rebuild.py
```

原生 ARM64 运行演示：

```bash
cd deliverable
./minilua smoke.lua
# smoke: 10 42 3.5 3.75
```

在 x86_64 Linux 上，构建脚本仍生成 AArch64 ELF，并自动使用 `qemu-aarch64 -L /usr/aarch64-linux-gnu` 运行测试；可用环境变量 `AARCH64_SYSROOT` 指定匹配的运行 sysroot，并用 `AARCH64_SYSROOT_SOURCE` 记录自定义 sysroot 的来源。此路径已提供，当前交付的实测结果来自原生 ARM64。sysroot 的来源包、实际路径和每次测试命令会写入报告。交付文件依赖 AArch64 glibc loader、libc 和 libm，引用的符号版本见 `reports/elf.json`。版权与许可声明见 `src/LICENSE.lua`，分发程序时一并附带。

## 文件位置

|位置|内容|
|---|---|
|`sources.json`|固定的源码/工具 commit、下载 URL、原始 SHA-256、随机种子|
|`src/`|原始源码、格式化后的开发源码、包含排版与两处 noinline 的补丁、Lua 许可证|
|`patches/`|混淆器白名单补丁与上游许可证|
|`.cache/`|可重新获取的混淆器源码和插件构建缓存|
|`reference/`|未剥离文件、基线 stripped、LLVM bitcode/IR、目标文件、完整/目标函数反汇编|
|`reports/`|构建日志、测试原始输出、ELF 检查、CFG/大小指标、验收报告|
|`manifest.json`|实际工具版本、环境、完整参数/命令、结果及文件 SHA-256|
|`deliverable/`|混淆后的 minilua、smoke.lua；运行说明见本文，版权声明见 `src/LICENSE.lua`|

`reference/`、`reports/`、`.cache/` 为本地生成目录，已加入此示例的 `.gitignore`；源码、补丁、脚本、交付文件与 manifest 可随项目保存。构建不删除交付目录中的未知文件，大小检查会拒绝多余文件、软链接或特殊文件。

重新构建会清除旧的 LightRE 评估与重复构建报告；可在构建后按需重新运行对应脚本。

## 用 LightRE 人工测试

小函数测试对象是 `deliverable/minilua`。在对话中发送下面这段即可按项目入口分析并打开页面：

```text
读取 /work2/LightRE/SKILL.md，分析 /work2/LightRE/samples/minilua/deliverable/minilua，workspace 使用 /work2/minilua-analysis，然后打开结果供我查看。
```

已有样本可直接使用。只有缺少文件或需要修改样本时才重新构建。传具体 ELF 文件路径；LightRE 的目录选择逻辑寻找 `.so`，不要只传 `deliverable/`。

按测试目的选择文件：

|测试目的|文件|luaH_get RVA / 指令数|luaH_set RVA / 指令数|
|---|---|---|---|
|小函数测试：混淆且无普通符号|`deliverable/minilua`|`0x85a0` / 526|`0x8ea8` / 138|
|核对混淆函数名称与边界|`reference/obfuscated.full`|`0x85a0` / 526|`0x8ea8` / 138|
|对照未混淆逻辑|`reference/baseline.full`|`0x85a0` / 112|`0x8830` / 41|
|对照符号剥离的影响|`reference/baseline.stripped`|`0x85a0` / 112|`0x8830` / 41|

这些数值来自当前构建的 GNU objdump 参考。重新构建后以 `reports/cfg.json` 和 `reference/*.asm` 为准；LightRE 识别出的函数边界可能存在差异。stripped 文件显示自动生成的名称，应按 RVA 搜索。

人工查看时，先搜索 `0x85a0`，核对 LINES 与滚动加载后实际显示的指令，再查看 `0x8ea8`。当前混淆版 luaH_get 的 526 条参考指令超过单页 200 条，适合检查滚动追加。需要定位差异时，对照 `reference/obfuscated.luaH_get.asm`、`reference/obfuscated.luaH_set.asm` 和可读的 `src/minilua.c`。比较基线版时，在同一个外部 workspace 中显式分析所选文件，再以返回的 SO 结果目录重启 Web。当前页面每次只浏览一个结果目录。

终端入口先导出：

```bash
python3 /work2/LightRE/scripts/analyze.py \
  /work2/LightRE/samples/minilua/deliverable/minilua \
  --workspace /work2/minilua-analysis
```

命令返回实际的 SO 分析目录后，用它启动只读 Web：

```bash
/work2/LightRE/tools/venv/bin/python /work2/LightRE/scripts/web.py \
  "<上一步返回的 SO workspace 路径>" --host 0.0.0.0 --port 8000
```

Web 接受已导出的结果目录；不会自动分析 ELF。日常打开不需要运行构建脚本或测试套件，也不自动导入其他参考版本。

如需核对机器码，可手动运行以下比较工具，传入已经导出的单个 SO 结果目录：

```bash
python3 scripts/check_lightre.py --workspace "<SO workspace 路径>"
```

工具按 SHA-256 确认样本，读取保存的汇编，与 GNU objdump 参考逐地址比较，输出遗漏、额外指令和机器码差异到 `reports/lightre.json`。它只读已有工作区，不启动 Rizin、Web 或浏览器；发现差异时返回 1。需要重新导出时，使用分析入口的 `--force`，再刷新页面。

此前一次实测中，混淆版两个目标函数读取完整；基线 luaH_get 返回 102/112 条，缺失 `0x86b0` 至 `0x86d4` 的 10 条指令。这是历史定位线索，当前结果应人工查看或对导出数据按需比较。函数浏览与汇编读取仍属于 V0.1 验收，反编译和去平坦化尚未实现。

## 混淆器核查

采用 [eshard/obfuscator-llvm 固定版本](https://github.com/eshard/obfuscator-llvm/tree/be910da9267b40cbe6ae8c946eb2ef008f6e55f4)。已阅读该 commit 的 `README.md`、`CMakeLists.txt`、`Plugin.cpp`、`flattening/Flattening.cpp`、`utils/Utils.cpp` 与 `utils/CryptoUtils.cpp`：

- 文档使用 LLVM 14.0.6；本次使用匹配的 LLVM 14 开发包实际构建插件，并检查 LLVM 注册的 AArch64 后端。
- pass 的真实注册名为 `flattening`，支持新 pass manager 的 `opt -load-pass-plugin`。本例显式运行 `-passes=function(flattening),verify -verify-each`，不使用 `-mllvm -fla`。
- 上游 `toObfuscate(true, ..., "fla")` 默认选择全部函数，支持 `nofla` 注解；且入口先执行 `LowerSwitchPass`。因此补丁在它之前增加 `LLVM_OBF_FUNCTIONS` 精确函数名白名单，未设置时不选择函数，避免其他函数被前置 pass 修改。
- pass 在 Clang 的 `-O2/-Os` IR 优化之后、后端生成之前执行，避免后续 IR 优化还原状态机。`LLVM_OBF_SEED` 固定状态编号的随机种子；该 AES PRNG 只用于编译时生成编号，没有对目标程序的字符串做加密。
- 基线和混淆版均使用两处相同的 `noinline`；检查 pass 日志、逐函数 IR 内容（解析 metadata/attribute 编号）及最终机器码。除两个目标外，203 个函数 IR 一致；`luaV_execute` 被 Clang 内联进 `luaD_call`，该函数未被 pass 修改。

混淆后机器码使用状态加载与比较树分派，而非间接跳转表；有无 `br` 不能单独判定平坦化成败。`reports/verification.md` 包含两函数的代码大小、IR 基本块数、间接跳转数、分派入口和返回分派的分支证据。机器码 CFG 基本块数未可靠测量，报告明确标注。

大函数版额外核查了同一固定 commit 的 `split/SplitBasicBlocks.cpp`。`patches/obfuscator-split-filter.patch` 为它加上相同的函数白名单，并修复 `splitN` 被前一个短基本块永久缩小的问题。其 `split_num` 参数通过同时使用 `opt -load` 和 `-load-pass-plugin` 注册，实际流水线为 `function(split-basic-blocks,flattening,flattening),verify`。两轮平坦化均经过 LLVM verifier；最终仍检查状态槽、状态写入和机器码共享分派入口。
