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
