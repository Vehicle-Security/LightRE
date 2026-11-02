## 目标适配：native-smoke

这是开发自测库，不是 DG 或 minilua。使用 Android ARM64 / API 23、
unidbg 0.9.9、Unicorn2；无需 Dalvik VM、JNI 或 JNI_OnLoad。
每次新建模拟器，用 `loadLibrary(SO, false)` 加载固定测试库。

已知入口和 ABI（以只读 contract.json 为最终依据）：

- `smoke_arithmetic(int32_t a, int32_t b) -> int64_t`：先扩展到 64 位再算 a*3-b；注意参数顺序。
- `smoke_transform(const uint8_t*, uint32_t length, uint8_t*, uint32_t capacity, uint32_t key) -> int32_t`：
  output[i] = input[i] XOR ((key+i)&255)，容量不足返回 -1，否则返回长度。
- 输入/输出必须分配模拟内存，空输入也分配至少 1 字节；读取有效长度，最后释放。

Java 入口 `lightre.NativeHarness SO request.json observation.json`。
request 的 a、b 为 int32，key 为 0–255，capacity 为非负整数，input_file 相对请求文件解析。
observation 输出 integer（int64）、length（int32）、output_hex（小写，失败长度 -1 时为空）。
Python 外部核对普通输入、不同参数、整数边界/空输入、容量不足四例。
不得硬编码 observation、跳过真实调用或改动预期取得通过。

构建：`mvn -B -ntp compile dependency:build-classpath -Dmdep.outputFile=classpath.txt`。
通过下文 LightRE 的 run/repair/replay 入口运行；SO、请求和输出路径由工具传入。
