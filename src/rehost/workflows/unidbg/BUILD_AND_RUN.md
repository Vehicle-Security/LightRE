# 生成工程与真实执行

公共技术栈为 Java/Maven、unidbg Android ARM64 与 Unicorn2；版本见同目录 pom.xml。
它只提供依赖和编译插件，不预置加载顺序、JNI、目标地址或业务参数。

1. 依据 DISCOVER 的证据实现 lightre.RehostHarness。main 接收 SO、request.json、observation.json。
   创建 ARM64 Android 模拟器、必要 resolver；根据实际依赖决定是否创建 Dalvik VM，
   加载真实模块并执行所需初始化，再按真实符号/JNI/已调查 RVA 调用入口。
2. 真正分配模拟内存并写入输入，记录长度和必要对象关系；读回实际返回、有效缓冲区及副作用。
   所用输入是执行器固定后的文件，不能从原路径另取一个“容易成功”的种子。
   用实际调用/Hook 计数和日志标明已经进入任务入口，不能只把加载成功当作调用完成。
3. 在工程内运行 `mvn -B -ntp compile dependency:build-classpath -Dmdep.outputFile=classpath.txt`，
   再以 `java -cp target/classes:<classpath.txt内容> lightre.RehostHarness SO request.json observation.json` 调试。
   固定请求参考只读任务契约；调试输出写到工程内的 scratch 目录，不写历史 Run 或外部检查报告。
4. 编译/API 错误先查实际依赖源码或 javap；加载、系统调用或 JNI 失败转 ENVIRONMENT，
   补足一个具体缺口后重放同一输入，确认没有换成绕过目标的路径。
5. 正常返回时写完整 JSON observation；异常和未知环境必须保留具体错误并使进程失败。
   目标本来定义的错误返回照常记录，不与加载/执行失败混淆。不能抓住所有异常后输出成功。
6. 同步 HARNESS.md 与可复现命令。外层从 Run 快照重新构建并执行，
   固定预期存在时核对结果；否则仅记录 observed。运行与重放不代表已恢复 VM 业务源码。

调查与环境适配允许多轮交替。不要先构造通用 Android 模拟框架或为未出现的调用添加 stub。
