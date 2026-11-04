{{ DESCRIPTION if DESCRIPTION is not none else '调查目标 ARM64 SO，生成可真实运行的 unidbg harness，记录 ABI、初始化、依赖和补环境知识。' }}

目标 SO：{{BINARY}}
SHA256：{{SHA256}}
静态检查：{{INSPECTION}}
静态原始输出：{{INSPECTION_DIR}}
每次从零实现目标 Java harness，不复制已有目标的 Java 工程、固定环境或地址。
先调查实际入口与依赖，再通过真实 unidbg 运行逐项补环境。
检查模式：{{MODE}}；observe 仅保存运行观察，不能宣称行为验证或语义等价。
只读配套资料（含适用的 APK、DEX、资源或既有静态分析）：
{{MATERIALS}}

公共调用协议：`lightre.RehostHarness SO request.json observation.json`。
相对输入路径按 request.json 所在目录解析；调查脚本、小型资源放入 src/ 进入 Run 快照。
harness 是通用运行器：任意可读的 input_file 都必须能运行并在 observation 记录其
sha256 与长度（新字节码、变异输入都靠这个入口）。完整性校验只针对请求或契约
自带的期望哈希；不得在代码里硬编码"已知文件名白名单"拒绝未知输入——
防输入被调包 ≠ 只认固定夹具。
没有固定预期时，observation 至少包含：

```json
{"status":"returned","entry":"已调查的真实入口与 ABI","native_calls":1,"missing_environment":[],"result":{}}
```

native_calls 必须来自实际调用/Hook 计数，entry 必须有调查依据。目标错误也要如实保存；
缺环境或异常时令进程失败并输出具体位置/签名，不吞异常后返回成功。
调查性运行在 observation 标非空 `"mode"`（探针等干预实验），不算通过；
自然运行不得依赖探针兜底值。

缺口分两类记录（外层终态判定依赖此区分）：
- `missing_environment`：OS/系统环境缺口（system-service、libc、系统属性等）。
  必须补齐真实实现，非空则不算完成。
- `assumed_environment`：应用侧替身（目标 APK 内的 Java 业务回调，如应用自身
  类的方法）。无 APK 时不可能还原真实实现：实现合理替身并逐条记录
  签名、替身行为与依据；外层允许带此清单通过，但会原样呈报给用户。
