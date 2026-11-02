## 本项目由 rehost 从零生成

当前只有通用 Maven 依赖，没有目标 Java 实现。先读任务与静态检查，调查目标，
再在 src/main/java/lightre/RehostHarness.java 实现入口；不要寻找可复制的专用 harness。
每次新 start 创建独立工程，resume 继续当前工程；不把别的版本地址和环境当作本目标事实。

公共调用协议：`lightre.RehostHarness SO request.json observation.json`。
初始化、JNI、原生 ABI、输入传递和结果提取均随本目标调查与运行动态实现。
相对输入路径按 request.json 所在目录解析。所需代码/小型资源放入 src/；
运行过程、原始异常与缺失 JNI/系统接口写 stdout/stderr，不能吞异常后正常返回。

没有固定预期时，observation 至少包含：

```json
{"status":"returned","entry":"已调查的真实入口与 ABI","native_calls":1,"missing_environment":[],"result":{}}
```

这里只是运行观察协议；native_calls 必须来自实际调用/Hook 计数，entry 必须有调查依据。
目标错误也要如实保存；缺环境或异常时令进程失败并输出具体位置/签名，不填返回成功。
该 JSON 是 harness 提取的观察，不是独立语义证明。外层报告 observed、passed=false，
固定预期缺失时保持验证缺口，不把模型新写的预期作为基准。
