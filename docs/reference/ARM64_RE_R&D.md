# ARM64 `.so` 分析入门

定位：通用教程与背景参考，包含 Rizin、Frida 和 LLDB 等方法，不是 LightRE 当前功能、开发路线或验收依据。当前使用见 [V0.2 使用说明](../USAGE.md)，ARM64 + unidbg 产品方案从 [文档入口](../README.md) 选择。

本文介绍一套简单、实用的流程：用 Rizin 定位和阅读函数，用大模型辅助理解，再用 Frida 或 LLDB 验证关键结论。

> 只分析自己拥有或明确获准分析的程序。本文讨论的是通用的二进制调试和软件维护方法。

## 1. 先理解三类工具

### Rizin

Rizin 适合快速查看 ELF、函数、汇编、字符串和交叉引用。它是命令行工具，也有图形界面 Cutter。

### Ghidra / Binary Ninja

它们都可以反编译汇编。Ghidra 免费且功能完整；Binary Ninja 是商业工具，交互体验和分析速度通常更好。分析一两个函数时，不一定需要它们，Rizin 已经够用。

### Frida / LLDB

它们用于动态分析：让程序实际运行，在函数进入、返回或某个 API 调用时观察参数、内存和返回值。

可以把工具分工理解为：

```text
Rizin/Ghidra/Binary Ninja：代码可能怎么工作
Frida/LLDB：这次运行实际上怎么工作
大模型：帮助解释、命名、总结和提出假设
```

## 2. 静态检查 `.so`

先确认文件类型和架构：

```bash
file libtarget.so
readelf -h libtarget.so
```

ARM64 文件通常会显示：

```text
ELF 64-bit LSB shared object, ARM aarch64
```

查看符号、导入和导出：

```bash
readelf -Ws libtarget.so | less
nm -D libtarget.so | less
```

如果库被 strip 过，函数名可能已经消失。这时可以从字符串、导入 API、JNI 注册表和调用关系开始寻找目标函数。

```bash
strings -a -t x libtarget.so | less
readelf -Ws libtarget.so | grep -E 'JNI_OnLoad|Java_'
```

## 3. 用 Rizin 分析函数

启动自动分析：

```bash
rizin -AA libtarget.so
```

常用命令：

```text
afl                  列出已经识别的函数
s sym.target         跳到目标函数
pdf                  查看当前函数的反汇编
pdg                  查看伪代码，需要安装 rz-ghidra 等插件
axt                  查看当前地址的交叉引用
agf                  查看当前函数的调用图
izz                  查看字符串
ii                   查看导入函数
```

也可以直接从命令行导出：

```bash
rizin -AA -q -c 's sym.target; pdf' libtarget.so
rizin -AA -q -c 'aflj' libtarget.so
```

### RVA 和运行时地址

静态工具中常见的地址是模块相对偏移（RVA），例如：

```text
libtarget.so + 0x1234
```

程序运行时由于 ASLR，实际地址会变化：

```text
运行时地址 = 模块加载基址 + 0x1234
```

不要把静态地址直接当成运行时地址使用。

## 4. ARM64 阅读要点

ARM64 常见寄存器：

```text
x0-x7   前 8 个整数或指针参数
x0      通常也是返回值
x8      间接调用或系统调用中常见的临时寄存器
x29     frame pointer
x30     link register，也就是返回地址
sp      栈指针
```

常见指令：

```asm
ldr x1, [x0, #0x20]   ; 从 x0 + 0x20 读取一个值
str w2, [x0, #0x10]   ; 把 w2 写入 x0 + 0x10
bl  sub_func          ; 直接调用函数
blr x8                ; 跳到 x8 保存的地址，属于间接调用
cmp w0, #0            ; 比较
b.ne fail             ; 不相等则跳转
```

看到 `ldr xN, [x0, #offset]` 时，通常可以先假设 `x0` 是某个对象指针，但必须结合调用者和运行时数据确认。`0x20` 只是偏移，不能仅凭偏移确定字段含义。

## 5. 如何让大模型帮忙分析

不要只截取几行汇编。最好提供完整函数以及上下文：

- 函数地址和大小
- 完整反汇编
- 调用者和被调用者
- 使用到的字符串和全局变量
- 已知的导出符号或 API
- 如果知道，附上函数参数来源

可以使用下面的提示词：

```text
请分析下面这个 ARM64 函数。

要求：
1. 先描述确定的控制流和参数来源；
2. 推测每个栈变量和结构体字段的作用；
3. 给出接近 C 语言的伪代码；
4. 明确区分“确定事实”和“推测”；
5. 列出无法确定的地方，以及需要通过 Frida/LLDB 验证的内容；
6. 不要把猜测写成确定结论。

函数反汇编：
...
```

大模型适合解释和提出假设，但不能替代交叉引用、地址计算和运行时验证。特别是以下情况，单看函数本体容易误判：

- 间接调用和函数指针
- 复杂结构体
- 运行时解密的数据
- 依赖全局状态或环境的分支
- 被多个调用者复用的函数

### 函数级分析和 basic block

可以把完整函数作为交给大模型的基本单位，不需要人工逐个查看 basic block。但 basic block 仍然是函数控制流的底层事实：大模型在生成伪代码时，必须判断分支、循环、跳转目标和路径汇合位置。

例如下面的代码：

```asm
cmp  w0, #0
b.eq fail
...
b    done
fail:
...
done:
ret
```

如果只把指令作为线性文本提供，大模型需要自己推断：

- 哪些指令属于同一个基本块；
- 条件跳转的目标是什么；
- 哪些路径在 `done` 汇合；
- 是否存在循环、提前返回或跳转表。

短小、直白的函数通常可以直接分析：

```text
完整函数反汇编 → 大模型
```

中等复杂度的函数，最好补充调用关系和字符串：

```text
完整函数 + 调用者/被调用者 + 字符串引用 → 大模型
```

复杂函数则应让 Rizin、Ghidra 或 Binary Ninja 自动生成 CFG/basic block 信息，再交给大模型：

```text
完整函数 + CFG + 汇编或 IL + 数据引用 → 大模型
```

因此，函数是适合交给大模型的分析单位，basic block 是工具应该自动提取的上下文。你不必手工分析每个 basic block，但复杂函数不应只把线性汇编文本丢给大模型。

## 6. 用 Frida 验证函数

假设静态分析得到：

```text
库：libtarget.so
函数 RVA：0x1234
```

创建 `hook.js`：

```javascript
const moduleName = "libtarget.so";
const module = Process.getModuleByName(moduleName);
const target = module.base.add(0x1234);

console.log("module base:", module.base);
console.log("target:", target);

Interceptor.attach(target, {
  onEnter(args) {
    console.log("[target] enter");
    console.log("x0 =", args[0]);
    console.log("x1 =", args[1]);

    try {
      console.log("x1 string =", args[1].readUtf8String());
    } catch (_) {}
  },

  onLeave(retval) {
    console.log("[target] return =", retval);
  }
});
```

启动 Android 应用：

```bash
frida -U -f com.example.app -l hook.js --no-pause
```

附加到已经运行的进程：

```bash
frida -U -n com.example.app -l hook.js
```

如果函数有导出符号，也可以直接按名字 hook：

```javascript
const target = Module.getExportByName("libtarget.so", "target_function");
```

### Frida 能验证什么

可以观察：

- 实际参数和返回值
- 某个分支是否真的执行
- 某个对象偏移处的真实数据
- 是否调用了 `open`、`connect` 等 API
- 运行时解密后的字符串或配置
- 函数对内存和全局状态的修改

例如观察某个 C 字符串参数：

```javascript
Interceptor.attach(target, {
  onEnter(args) {
    try {
      console.log(args[1].readUtf8String());
    } catch (_) {}
  }
});
```

如果函数调用频繁，应增加过滤条件，只打印感兴趣的调用，避免日志过多影响程序行为。

## 7. 用 LLDB 单步验证

LLDB 适合需要逐条执行时使用。附加进程后：

```text
image list
breakpoint set --name target_function
continue
register read x0 x1 x2 x3 x4 x5 x6 x7
memory read --format x --size 8 --count 8 $x0
stepi
finish
register read x0
```

如果没有符号，需要先找出模块加载基址，再用“基址 + RVA”设置断点：

```text
breakpoint set --address 0x7abc1234
```

## 8. 什么时候必须动态验证

简单的 getter、数学函数和直白的字符串处理函数，通常静态分析就足够。

以下情况值得使用 Frida 或 LLDB：

- 不确定参数究竟是什么类型
- 不确定某个偏移对应哪个字段
- 存在 `blr xN` 等间接调用
- 字符串或配置运行时生成
- 分支依赖设备、环境或初始化状态
- 需要确认函数的真实返回值和副作用
- 静态伪代码看起来合理，但无法确认

动态分析的目标不是“所有函数都 hook”，而是验证最关键的几个假设。

## 9. 推荐工作流

```text
1. file/readelf 确认 ELF 和 ARM64 架构
2. Rizin 找函数、字符串、导入和交叉引用
3. 导出完整函数和上下文
4. 让大模型解释并标注不确定点
5. 用 Frida 打印参数、返回值和关键内存
6. 必要时用 LLDB 单步
7. 回到静态工具修正函数名、类型和结构体
```

最小工具组合是：

```text
Rizin + LLM/Agent
```

需要参考反编译时使用 Ghidra 或 Binary Ninja。LightRE 的动态实验以 unidbg/Qiling 等为主，不依赖 Frida/LLDB；本文此前的真实环境验证示例可作为通用知识阅读。

## 10. 全 Headless 二次开发和 LLM 集成

LightRE 采用 LLM/Agent 主导、Rizin 优先、Ghidra Headless 按需的方式。具体设计和验收范围以 [设计文档](../design/ARCHITECTURE.md) 和 [需求清单](../REQUIREMENTS.md) 为准。

### 工具职责

```text
LLM/Agent
  └─ 提出假设、选择工具、设计实验、去混淆和重写函数

Rizin（默认静态入口）
  └─ 读取函数、汇编、字符串和引用，按需深入分析

Ghidra Headless（按需）
  └─ 参考伪代码、P-Code、类型和控制流分析

unidbg/Qiling 等
  └─ 执行实验，结合符号/强制执行恢复混淆逻辑

文件 + SQLite
  └─ 保存函数版本、知识、证据和续做资料
```

Ghidra 支持使用 `analyzeHeadless` 批量导入 `.so`、运行分析和执行脚本，不需要启动图形界面。脚本可以访问函数、指令、基本块、调用者、被调用者、字符串引用和数据类型；使用 `DecompInterface` 可以批量获得函数的 C-like 反编译结果。

适合交给 LLM 的函数资料可以按需输出为 JSON，下面仅为输出示例，无需额外持久化一份上下文数据库：

```json
{
  "module": "libtarget.so",
  "function": {
    "name": "FUN_00101234",
    "address": "0x101234",
    "size": 96
  },
  "decompiled_code": "...",
  "assembly": "...",
  "callers": [],
  "callees": [],
  "strings": [],
  "references": [],
  "cfg": "..."
}
```

让 LLM 返回结构化结果，比只返回一段散文更适合后续处理：

```json
{
  "summary": "...",
  "parameters": [],
  "return_value": "...",
  "side_effects": [],
  "evidence_ids": [],
  "uncertain_points": [],
  "dynamic_tests": []
}
```

### Ghidra Headless 的适用场景

- 免费，适合长期运行和部署；
- 有完整的反编译、类型、CFG 和调用关系 API；
- 适合批量处理大型项目；
- 可以通过脚本保存和重复使用分析结果；
- 更容易搭建服务化或 CI 流程。

Binary Ninja 的 Python API 和 IL 也很适合自动化，并且交互和分析速度通常很好。但如果完全不使用界面，且需要长期批量部署，应先确认商业授权范围。它的主要优势更多体现在交互体验和 API 易用性上。

LightRE 的最小分析闭环是：

```text
Agent 选定函数和问题
    ↓
读取 Rizin 结果，按需调用 Ghidra 或执行实验
    ↓
根据证据重写并检查函数
    ↓
保存文件和 SQLite，继续迭代，Web 展示结果
```

不要求先完成整个 SO 的 Ghidra 分析，也不要求每个函数都使用所有工具。已有结果直接读取，避免重复完整分析。

### 安装依赖

LightRE 的 `lightre.deps` 面向 Ubuntu 24.04 容器，会：

- 安装 Java 21、Python、curl、unzip 等基础依赖；
- 检查现有 Rizin；如果没有则从源码构建；
- 从 Ghidra 官方 GitHub Release 下载最新公开发行版；
- 将工具安装到项目的 `tools/` 目录；
- 由 `lightre` 和 `paths.py` 自动解析工具目录，不依赖 shell 环境注入。

执行：

```bash
cd LightRE
lightre <workspace>

"$LIGHTRE_TOOLS_DIR/rizin/bin/rizin" -v
"$LIGHTRE_TOOLS_DIR/ghidra/support/analyzeHeadless"
```

`lightre.deps` 是系统依赖的统一安装模块；LightRE 运行时通过 `LIGHTRE_TOOLS_DIR`、`GHIDRA_HOME` 和 PATH 解析工具，不需要 source 环境脚本。

缺少依赖时服务需要联网，并可能需要 `sudo` 安装 Ubuntu 系统依赖。工具安装在配置的 tools 目录，可通过环境变量覆盖 Ghidra 下载地址：

```bash
GHIDRA_URL=https://example.org/ghidra.zip lightre <workspace>
```

服务支持重复启动：已经存在且可用的 Rizin 或 Ghidra 会被复用，不会重复下载。

## 11. 常见问题

### 函数名全是 `fcn.xxxxx`

说明符号可能被 strip。使用字符串、导入 API、JNI 注册和交叉引用定位函数，并自行重命名。

### `pdg` 没有输出

通常是没有安装对应的 Rizin decompiler 插件，或者函数分析不完整。先运行 `-AA`，仍然不行时直接阅读 `pdf`，或导入 Ghidra。

### Frida hook 不到函数

检查以下几点：

- 库是否已经加载
- 模块名是否正确
- 使用的是 RVA 而不是错误的绝对地址
- 目标函数是否经过内联、跳板或重定位
- 是否需要等待 `dlopen` 后再 hook

### LLM 给出的伪代码不可信

把完整函数、调用关系和数据流一起提供，并要求模型区分事实与推测。对关键结论使用 Frida/LLDB 验证，不要只依据伪代码下结论。
