# 通用 harness 执行契约

`src/execution/` 不识别目标名称，不包含样本 ABI、JNI 注册、VM 地址、业务预期或产物名。当前运行后端是 Android ARM64 SO + Maven/Java/unidbg；“通用”指该后端内可接入新目标，不表示支持其他架构或构建系统。

核心负责构建、执行、进程超时与取消、日志、锁、快照、结果比较和重放。目标适配由外部 Java harness 承担；专用参数和检查规则由显式配置的独立 Python 检查器承担。仓库 `examples/` 保存示例，没有工具内置目标白名单。

## 关联任意目标

先生成说明，再按调查结果创建或适配工程：

```bash
tools/venv/bin/python scripts/harness.py guide /workspace/harnesses/my-target \
  --target my-target --instructions /workspace/my-target-adaptation.md

tools/venv/bin/python scripts/harness.py configure /workspace/my-so \
  --harness /workspace/harnesses/my-target --main-class company.MyHarness \
  --cases /workspace/my-cases.json --checker /workspace/checkers/my-target.py \
  --artifact result.dat --artifact traces/events.json
```

`--instructions`、`--checker`、`--artifact` 都可省略。传新 SO 文件时另加 `--workspace`，导入原 workspace。`configure` 关联现有工程，生成缺失说明，不生成目标 Java 实现，不覆盖不同的已有配置。

生成的 `harness.json` 示意：

```json
{
  "path": "/workspace/harnesses/my-target",
  "parameters": {"main_class": "company.MyHarness"},
  "cases": "/workspace/my-cases.json",
  "checker": "/workspace/checkers/my-target.py",
  "artifacts": ["result.dat", "traces/events.json"]
}
```

路径相对于 `harness.json`，也可使用绝对路径。`main_class` 必须明确指定；`artifacts` 中所有文件都必须生成，保存 SHA256，重放要求字节一致。路径必须位于 Run 内，不能覆盖工具输入、检查器或记录，也不能通过符号链接指向外部。用例和检查器必须位于可编辑 harness 之外，修复模型不能修改这些基准。可用 `protected_files` 数组额外声明修复任务需固定的文件。

Java 调用协议统一为：

```text
java -cp <classes:dependencies> <main_class> SO request.json observation.json
```

入口处理自己的 ABI、JNI、初始化和参数，向第三个路径写 JSON observation；相对请求路径的输入文件由 harness 读取。产物写入 observation 所在 Run 目录，文件名由本目标约定。

## 用例与默认检查

用例 JSON 是非空数组，`id` 唯一：

```json
[
  {
    "id": "seed",
    "request": {"input_file": "inputs/seed.bin", "mode": 3},
    "input_sha256": "<输入文件 SHA256>",
    "expected": {"status": 0, "result": "known output"}
  }
]
```

用例中的相对输入路径按用例 JSON 所在目录解析。`run --request` 的相对输入路径按请求 JSON 所在目录解析。`input_hex` 也可提供字节；无二进制输入时可只传 JSON 参数，输入 SHA256 使用空字节的哈希。

没有专用检查器时，核心只规范化可选输入文件，其他参数作为不透明 JSON 传给 Java。输入哈希和参数（包括嵌套值类型）必须精确匹配已声明用例；`expected` 必须是非空对象。结果逐字段比较值和类型，未列入预期的观测字段可保留。

## 专用检查器

检查器是可信、固定的单个 Python 文件，只依赖标准库，不依赖同目录模块或 LightRE 包；它作为代码运行，不是沙箱内的配置。需要专用参数校验、计算型预期、JNI/VM/文件结构校验时实现：

```python
def normalize(request, base=None):
    # 返回规范化请求 dict、实际输入 bytes、来源 dict 或 None。
    # 保存输入的统一请求路径为 input.bin；路径按 base 解析。
    return normalized, data, origin


def expected(normalized, data, rows):
    # 从固定用例取得预期，或按已知独立基准计算；不能信任 harness 自报通过。
    return nonempty_expected_dict


def validate(observation, run_root):
    # 追加目标专用差异。通过时返回空列表。
    return list_of_difference_strings
```

外层仍比较预期并核对所有声明产物、SO、源码、输入、检查器和依赖完整性。检查器异常记录为失败。检查器的输出不能代替真实 harness 执行。

每次 Run 保存 `contract.json`（用例、入口、产物声明）、`checker.py`（若配置）、`expected.json`、请求与输入、Java 工程和说明；manifest 保存其哈希。重放使用历史检查器与用例快照，不读取当前版本的检查器或原输入。SO 和 Maven 依赖仍须保留在记录的路径。

旧实现保存的 Run 缺少检查器快照，不能证明历史检查规则。它们保留只读；新执行器明确拒绝直接重放，不按目标名称推测检查器。将现有工程配置为显式契约后重新运行，取得完整的新 Run。旧修复任务绑定旧检查器/执行代码的哈希；核心变更后应使用现有工程和新契约建立新任务，历史任务保留。没有缺失契约的历史 Run 即使缺少 HARNESS.md，仍可重放且不会补写历史说明。

## 示例与修复

```bash
# 开发测试库的构建和样本规则位于 examples，通用 CLI 不负责生成固定样本。
tools/venv/bin/python examples/native-smoke/prepare.py --workspace /workspace

# DG/minilua 的便捷配置同样是样本脚本。
tools/venv/bin/python examples/configure.py <SO目录> --kind dg --programs <bin目录>
tools/venv/bin/python examples/configure.py <SO目录> --kind minilua

# 任意已关联目标使用同一执行流程。
tools/venv/bin/python scripts/harness.py check <SO目录>
tools/venv/bin/python scripts/harness.py run <SO目录> --case seed
tools/venv/bin/python scripts/harness.py replay <SO目录> <Run目录>
tools/venv/bin/python scripts/harness.py repair <SO目录> --max-rounds 3
```

修复循环使用配置的固定用例、检查器及输入快照；保持基准只读。缺少契约时失败，不退回开发样本。接入循环不代表新目标已经能自动修好，也不代表支持候选构建或差分。
