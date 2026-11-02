
1. 项目需要人可读可维护，因此需要保持简洁，不能过度设计。


2. 这个项目的目标是实现so的分析，所有操作最终都需要自动化，工具化，可复现。所以对于一些Agent操作，最终需要实现为功能。


3. Keep it sample, keep it stupid.

4. V0.1 已作为基础版本提交；当前 V0.2 按最新用户反馈限定为初步候选与 Web 审阅：默认 Ghidra 直接反编译 + 一轮 LLM 保守整理；首轮既无 Ghidra 初稿也无已有候选的函数自动分流到完整汇编输入，将全部地址和指令一次性内嵌到同一提示词，开放只读工具，单批 600 秒，不再只给首尾片段。也支持对已有工作区显式选择纯汇编 LLM 模式，不读取或调用 Ghidra 伪代码。允许保留混淆结构，去混淆与 VM 语义恢复留到后续版本。新任务不自动追加深入续做，旧任务保留记录的策略。V0.2 已于 2026-11-01 通过用户人工验收，范围见 docs/milestones/v0.2/PLAN.md；DG 核心巨函数未恢复的 C 留到后续差分阶段处理。后续版本推进需用户确认。

5. 测试保持轻量，优先保证后端数据与 API 正确：使用临时 SQLite、源码文件及 FastAPI TestClient，直接检查真实后端路由与持久化，不启动浏览器或 HTTP 服务，不依赖真实样本、分析工具或模型。不能 mock 掉被验证的映射与发布逻辑；未实现的功能单列待验，不以 mock 或跳过用例宣称通过。两个 workspace Markdown 只是人工启动快捷指令，不是自动验收流程；除非用户明确要求，不运行 Playwright、浏览器或整套 Web 测试。

6. 长期自动化逆向以 docs/GOAL_LOOP.md 为支柱方案：Python 循环调用 codex exec，实际验证未通过时自动续跑，最终交付可编译、可维护的源码工程。该能力尚未实现；不要将当前单轮候选生成当作完整 Goal Loop。Web 保持辅助定位，不以扩充 UI 为前置；工具开发的小步交付不意味着成熟工具执行目标时需要人工逐轮确认。

7. 用户已授权开始 V0.31，范围只包含专用 Android ARM64 测试 SO 的 unidbg harness、Python 运行/记录/重放及 harness 修复循环。入口为 scripts/harness.py，实现位于 src/execution/；范围见 docs/milestones/v0.31/PLAN.md，命令见 USAGE.md，真实结果见 DELIVERY.md。固定 SO、预期与外部检查器不由模型修改。开发自测不代表人工验收，不扩展到 DG JNI、minilua Android 适配或候选差分。

8. 工具的运行与安装环境为受控 Linux 容器。系统依赖统一维护在 scripts/install_deps.sh，包括 v0.31 的 JDK、Maven、Clang 和 LLD；不要只临时安装或另建独立安装入口。Codex 执行默认沿用容器的 danger-full-access 模式，避免嵌套 namespace 限制；保留显式配置覆盖，固定基准仍由工具核对。

9. 已有目标的动态运行复用原来的 workspace。DG 与 minilua 分别维护 harness：DG 需要 JNI，minilua 走 Android ARM64 SO 的薄 C 接口，不需要 JNI；各自通过 SO 子目录的 harness.json 关联，运行记录保存到各自 runs/。native-smoke 只用于开发自测，不迁作真实目标 harness，不以自测通过宣称目标已运行。真实目标适配仍遵循相应版本范围。

10. minilua 为支持 unidbg 所需的薄 C 接口、条件编译、Android SO 构建脚本及用例应在原 samples/minilua/ 源码项目中维护，保留原 Linux 构建。构建后的 SO 再导入原分析 workspace；workspace 保存 Java harness 和运行记录，不另建一套可编辑的 minilua 适配源码，也不将该适配放进通用工具模块。

11. 每个 harness 工程根目录维护 HARNESS.md。新建先生成说明，已有先读并核对适用性，修复前更新适配计划，随代码同步 ABI、初始化、命令和缺口。保留已有经验；Run 快照含执行前说明，历史 Run 不补写。真实检查由外部执行器记录到当前说明的检查区，不能将生成文档当作目标适配完成或运行通过。

12. 用户随后明确授权按上述规划配置 DG 和 minilua 的真实 unidbg harness，范围扩展到这两项目标的运行适配、原 workspace 关联和真实输入重放。遵循第 9–11 条的目录归属；第 7 条的历史 v0.31 开发边界不阻止本次授权。DG 必须调查真实 JNI 注册/初始化和 .bin 执行，不以通用返回零的 stub 宣称通过；minilua 在原源码中增加 Android SO 薄接口，保留 Linux 样本。候选恢复、双边差分及完整业务语义恢复仍不包含在本次配置请求中。

13. 执行核心必须保持通用：src/execution/ 与 scripts/harness.py 不识别 DG、minilua 或 native-smoke，不内置目标类型白名单、ABI、参数语义、RVA、预期公式或产物名。harness.json 显式配置 Java main_class、用例、独立检查器和产物列表，规则随目标/示例维护；示例准备和便捷配置在 examples/，minilua C/构建仍在 samples/minilua/。检查器独立于模型可编辑工程，随用例进入 Run 快照并核对哈希；缺失契约不默默回退某个样本。repair 使用关联目标的固定契约，具体协议及旧记录限制见 docs/HARNESS_CONTRACT.md。本条是用户后续要求的通用化修正，覆盖早期 native-smoke 专用入口限制。
