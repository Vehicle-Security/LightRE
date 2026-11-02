## 目标适配：native-smoke

这是开发自测库，不是 DG 或 minilua。使用 Android ARM64 / API 23、
unidbg 0.9.9、Unicorn2；无需 Dalvik VM、JNI 或 JNI_OnLoad。
每次新建模拟器，用 `loadLibrary(SO, false)` 加载固定测试库。

已知入口和 ABI（以只读 contract.json 为最终依据）：

- `smoke_arithmetic(int32_t a, int32_t b) -> int64_t`：先扩展到 64 位再算 a*3-b；注意参数顺序。
- `smoke_transform(const uint8_t*, uint32_t length, uint8_t*, uint32_t capacity, uint32_t key) -> int32_t`：
  output[i] = input[i] XOR ((key+i)&255)，容量不足返回 -1，否则返回长度。
- 输入/输出必须分配模拟内存，空输入也分配至少 1 字节；读取有效长度，最后释放。

历史 Java 入口为 `lightre.NativeHarness SO request.json observation.json`；当前 rehost 从零生成的入口为 `lightre.RehostHarness`。
request 的 a、b 为 int32，key 为 0–255，capacity 为非负整数，input_file 相对请求文件解析。
observation 输出 integer（int64）、length（int32）、output_hex（小写，失败长度 -1 时为空）。
Python 外部核对普通输入、不同参数、整数边界/空输入、容量不足四例。
不得硬编码 observation、跳过真实调用或改动预期取得通过。

构建：`mvn -B -ntp compile dependency:build-classpath -Dmdep.outputFile=classpath.txt`。
通过 LightRE 的 run/repair/replay 入口运行；SO、请求和输出路径由工具传入。

### 准备开发自测

仅用户要求自测时，从 LightRE 根目录执行：

```bash
tools/venv/bin/python examples/native-smoke/prepare.py --workspace <外部workspace>
tools/venv/bin/python scripts/rehost.py start <返回的SO分析目录> \
  --goal '调查并实际调用测试库入口，按固定用例和独立检查器核对输出' \
  --material examples/native-smoke/ADAPTATION.md \
  --cases examples/native-smoke/cases.json --checker examples/native-smoke/checker.py
tools/venv/bin/python scripts/harness.py check <返回的SO分析目录>
```

首次普通 run 使用 a=7、b=-3、key=90、input_hex=00017f80ff，预期整数 24、长度 5、输出 5a5a23dda1。参数 a/b 是 int32，key 为 0–255，capacity 默认输入长度；输入与容量上限 1 MiB。文件输入和 input_hex 恰选其一。可自定义请求，独立 checker 按已知公式计算预期；该能力仅属于本样本，不是工具默认 ABI。

准备脚本只构建并导入固定测试 SO，记录构建输入、工具链和 SO 哈希，不创建或关联 Java 工程。rehost 在选定 workspace 的 harnesses 下创建独立工程；新生成流程尚无真实模型/unidbg 集成通过记录。它不是任意 Android SO 构建器，也不是逆向候选构建。通用命令与记录只在[功能说明](../../docs/USAGE.md)维护。
