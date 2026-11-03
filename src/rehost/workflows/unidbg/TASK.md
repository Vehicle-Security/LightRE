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
没有固定预期时，observation 至少包含：

```json
{"status":"returned","entry":"已调查的真实入口与 ABI","native_calls":1,"missing_environment":[],"result":{}}
```

native_calls 必须来自实际调用/Hook 计数，entry 必须有调查依据。目标错误也要如实保存；
缺环境或异常时令进程失败并输出具体位置/签名，不吞异常后返回成功。
