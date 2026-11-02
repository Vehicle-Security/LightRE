## minilua 适配依据与历史运行（Android SO，纯 C 接口）

薄 C 接口仍在原源码项目维护。以下 Java 调用约定记录 2026-11-01 的已运行工程；新建目标通过 [rehost](../../docs/USAGE.md#rehost-从零生成) 从零调查并生成 harness，不再复制样本 Java 模板。旧 workspace 工程和 Run 保留为历史证据。

工程归属原 minilua workspace 的 `harnesses/minilua/`。这是纯 C/native 目标，
不创建 Dalvik VM，不实现 JNI，不调用 JNI_OnLoad。
当前 Linux glibc AArch64 PIE 不能直接作为 Android SO 加载到 unidbg。

薄 C 接口、条件编译、Android ARM64 SO 构建脚本和用例应维护在原源码项目：
`{{LIGHTRE}}/samples/minilua`（源码放 src/，脚本放 scripts/），保留原 Linux 构建。
构建出的 Android SO 作为新目标导入原 workspace；这里不能另存一份可编辑的 C 适配源码。

源码 `src/unidbg_api.c` 包含原 minilua.c，导出已实现的薄接口：

- `int32_t lightre_minilua_run(const char*, uint32_t, const char* chunk, const char* argument)`：
  新建/关闭 Lua 状态，从内存编译执行源码，设置 arg[0] 和可选一个 vararg。
- `const char* lightre_minilua_result(uint32_t which)` 和
  `uint32_t lightre_minilua_length(uint32_t which)`：0=stdout、1=stderr、2=完整 Proto 文本。
- `void* lightre_minilua_vm_entry(void)`：实际 luaV_execute 地址，供 Java 观察入口命中。
- 返回 0 成功；正数为 Lua 解析/运行状态，负数为 ABI/内存/容量错误，不能作为预期脚本错误通过。
  每次最多 1 MiB 源码和每类输出，溢出明确失败，不截断后报成功。

准备构建依赖用统一安装入口 `scripts/install_deps.sh --android`；使用宿主 Clang/LLD
与官方 NDK r27d 的 Bionic sysroot，不在 ARM64 容器执行 x86_64 NDK 编译器。
构建命令：`python3 scripts/build_android.py --output <新的构建目录> --obfuscation small`，
可选 none 或 large；原 Linux 交付物不修改。每次保留独立 build.json 和源码副本。
`small` 仅平坦化 luaH_get/luaH_set；large 的配置和效果须以该次实际构建记录为准。

Java 使用 ARM64 / Android API23 / Unicorn2、unidbg 0.9.9，不创建 Dalvik VM。
调用入口为 `lightre.MiniluaHarness SO request.json observation.json`；
request 包含 input_file（相对请求文件）、chunk_name 和可选 argument。
观察 lua_status、exit_code、stdout/stderr、vm_entries；Proto 保存到 Run 的 proto.txt，
包含实际代码、常量及子函数关系。语法错误没有可执行 Proto，不要求 VM 入口命中。
通过目标的 harness-cases.json 核对完整输出和错误；不能把另一个 SO 的通过证据沿用到当前版本。

### 当前目标入口

先按[Android 构建说明](../../samples/minilua/ANDROID.md)在原源码项目构建独立 SO，再从工具根目录导入原 workspace 并生成 harness：

```bash
tools/venv/bin/python scripts/rehost.py start <新Android-SO> \
  --workspace <原minilua-workspace> \
  --goal '调查薄 C 接口，执行 Lua 输入，采集状态、stdout/stderr 与 Proto' \
  --input samples/minilua/tests/smoke.lua --material samples/minilua/ANDROID.md
```

上述命令没有独立预期，只收集运行观察，不能标记 passed。历史工程核对八个固定用例：smoke、tables、closures、errors、allocation、capabilities、syntax_error、runtime_error，closures 显式传固定参数；脚本源文件仍在 samples/minilua/tests。要复现固定预期检查，需提供模型不可修改的用例 JSON，并显式关联独立 checker。small/large 在确认兼容后可显式共享工程，每个 SO 保持独立关联和 Run；新 start 总是创建新工程。通用命令见[功能说明](../../docs/USAGE.md)，历史验证见[交付记录](../../docs/milestones/v0.31/DELIVERY.md)。
