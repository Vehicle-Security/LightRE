1. 保持项目简单、可读、可维护，不做当前不需要的框架和抽象。Keep it simple.

2. 工具目标是自动化、工具化、可复现的 SO 分析。已授权且反复需要的 Agent 操作最终应实现为功能，不能靠聊天手工代替。

3. 当前功能、命令、API 和兼容边界只维护在 docs/USAGE.md。docs 根目录只保留 README、USAGE、REQUIREMENTS 和 ROADMAP；架构及技术协议统一放在 docs/design/，入口为 docs/design/README.md。总体架构见 docs/design/ARCHITECTURE.md，后续顺序见 docs/ROADMAP.md；版本目录只保存范围、验收和交付记录，不再新建版本 USAGE。更新规则时修正当前正文与引用，不追加互相冲突的补充。

4. V0.2 已于 2026-11-01 通过用户人工验收：Ghidra 初稿加一轮 LLM 保守整理；首轮既无参考初稿也无候选时，全部已导出汇编一次性内嵌到同一提示词，保留只读工具，每批 600 秒。已有工作区也可显式使用纯汇编模式，不调用或读取 Ghidra 伪代码。允许保留混淆，新任务不自动深入续做，旧任务保留记录策略。DG 0x1c07c 无完整 C 的缺口留待后续差分，不用占位代码宣称恢复。

5. V0.31 已授权的范围包括通用 Android ARM64 harness 执行、记录、重放与修复循环，以及 DG JNI 和 minilua Android SO 的单边运行适配。新建目标使用 scripts/rehost.py 与 src/rehost/ 的代码和 Markdown workflow，从空工程调查、生成 harness 并动态补环境，不复制预制目标 Java；执行核心为 scripts/harness.py、src/execution/。范围和证据见 docs/milestones/v0.31/。仍待用户人工验收，开发测试不推进版本状态；旧适配运行结果不代替新生成链路的真实验证。候选构建、双边差分和完整业务语义恢复未纳入已交付范围。

6. 长期工程自动恢复以 docs/design/GOAL_LOOP.md 为支柱。产品目标是常驻 Python 服务、一个面向用户的指导聊天窗口及多个后台 Codex CLI 工作会话；提交任务后不依赖窗口保持连接或人工逐轮确认。当前只实现静态候选队列、harness 最小修复及独立 agents 基础层，不能宣称服务整合、聊天或完整工程闭环已完成；不自动开发未经授权的后续版本。

7. 测试保持轻量，使用临时 SQLite、源码文件、FastAPI TestClient 和外部边界替身，直接核对真实路由、映射、发布与持久化。不能 mock 掉被验证的核心逻辑，不能以跳过或替身宣称真实工具/模型已通过。除用户明确要求，不运行浏览器、Playwright 或整套 Web 测试。文档整理只检查引用、状态和实际 CLI，不因此启动恢复或运行样本。

8. 运行与安装环境是受控 Linux 容器。系统依赖唯一入口为 scripts/install_deps.sh，包含 JDK、Maven、Clang/LLD 及可选 Android sysroot；不临时另建安装入口。Codex 默认沿用 danger-full-access，不要求嵌套 namespace，保留显式覆盖。固定基准仍需核对。

9. 已有目标动态运行复用原 workspace。DG 与 minilua 各有 harness：DG 需要实际 JNI 调查；minilua 走 Android SO 薄 C 接口，不强加 JNI。每个 SO 的 harness.json 关联工程，运行记录在自己的 runs。native-smoke 仅作开发自测，不迁作真实目标 harness，不把其通过外推为目标已运行。

10. minilua 薄 C 接口、条件编译、Android SO 构建和用例维护在原 samples/minilua/，保留 Linux 构建。构建后导入原 workspace；workspace 只维护 Java harness 和运行记录，不另建可编辑的 C 适配源码，也不将其放入通用执行模块。

11. 每个 harness 根目录维护 HARNESS.md。新建先生成说明，已有先读并核对适用性；修复前更新计划，随代码同步 ABI、初始化、命令及缺口，保留有效经验。Run 保存执行前说明，历史不补写；外层维护当前说明的检查区，文档生成和模型声明不等于实际通过。

12. 执行核心保持通用，不内置样本类型白名单、ABI、RVA、预期公式、JNI/Proto 规则或产物名。harness.json 显式配置 main_class、用例、独立检查器与产物，规则在目标/示例维护。检查器和用例位于模型可编辑工程之外，随 Run 保存并核对哈希；无契约明确失败。修复循环消费目标固定契约，不能修改基准来取得通过。

13. DG 的注册、初始化和 .bin 执行必须调查并实际观察，不用通用零/null stub 或 patch ready 消除缺口。单边运行只说明所选输入与声明环境下可执行；DG/minilua 已有运行证据不代表二者的模型自动修复或业务等价已经验证。

14. 维护与优化以 docs/milestones/v0.31/PLAN.md 的收尾计划为准：作为容器内独立工具运行，业务模块保留 src 下现有职责目录，不为外部包调用统一搬入 src/lightre，也不要求先安装工具自身。agents 保留标准库 SQLite；正文、日志和产物留文件，单一状态只维护一个权威来源。本次不额外迁移既有静态/harness JSON 队列或合并所有数据库；先收敛重复执行代码、入口与无用文件，保留必要兼容及真实验证边界。

15. LightRE 集成和管理 Codex CLI；产品主入口是常驻 Python 服务及自己的指导聊天窗口，外部 Codex 读取根 SKILL.md 只保留旧方式兼容。main.py 启动 API/界面与任务主循环；指导会话理解用户目标并通过系统接口组织工作，后台会话按任务独立执行。agents 统一两类会话的模型执行及 SQLite 状态，execution 保留实际工具/原生运行、检查和重放，recovery 保留批次策略及候选/知识发布。空闲不循环调用模型，活跃工作实例有并发额度和项目锁；窗口断线不取消工作，重启核对租约后恢复。重复模型执行按 V0.31 计划收敛，内部 prompt/workflow 不另复制为产品 Skill；静态一轮策略及 V0.3 后续差分边界保持明确。
