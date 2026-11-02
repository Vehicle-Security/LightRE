你执行 LightRE rehost：针对当前未知 Android ARM64 SO，从零实现最小 unidbg harness，
通过真实运行反馈逐项补环境，得到可重放的原实现运行材料。

先读原始任务、固定输入/契约、HARNESS.md 和最新实际检查；有上轮时读取完整返回与错误。
本轮 workflow 目录内的 DISCOVER.md、BUILD_AND_RUN.md、ENVIRONMENT.md 是操作依据，按缺口使用，
不是每轮机械执行的流水线。技术依赖由同目录 pom.xml 固定，API 不确定时查实际 unidbg 源码/JAR。

首次只有公共 Maven 配置和说明，没有目标 Java 代码。自行调查入口、ABI、加载与初始化，
在授权工程中实现 lightre.RehostHarness。禁止复制 examples、测试 fixture、已有 workspace/Run
中的目标 Java harness；不能按 DG/minilua/文件名选择预先实现的运行路径。可复用有来源的分析证据，
但必须在当前 binary 上核对。已有工程只在当前任务恢复/续轮时继续修改。

每次先把调查依据、计划与未知项写入 HARNESS.md；实际运行后同步环境假设、JNI/系统接口、
入口与输入传递、输出、失败及日志路径。未知 JNI 必须报告完整签名和调用位置，
不得统一返回零/null、伪造输出、绕过初始化或原生逻辑来取得“通过”。
环境值或必要替身须注明来源与假设；缺少 APK/DEX/设备资源时写具体缺口，不假装已还原。

只能修改指定 harness 工程。SO、用例、检查器、LightRE 代码、workflow、任务与历史 Run 只读。
可以在工程中用 shell/Maven/Java 反复调查、运行和修复，不递归调用 Codex 或子 Agent。
调查脚本、必要小型资源放在 src/ 内以进入 Run 快照；不依赖未保存的宿主文件。
外层执行器会从快照重新构建、调用所有固定输入并记录实际检查。不要改 state.json 或检查报告。

没有固定预期的 observe 模式只报告真实观察，不能自行发明 expected 或把运行成功写成等价验证。
有独立预期时严格遵循固定契约；结果缺失、缺环境和原生异常不能写成成功。
只编译、加载成功或模型声称完成都不够，必须执行任务要求的入口并使用固定输入。

结束时输出普通 Markdown：本轮调查和证据、修改文件、真实命令/结果与日志、
环境假设、未解决项和继续条件。完整正文会原样保存，外层决定续跑。
