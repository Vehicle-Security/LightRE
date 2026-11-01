

# LightRE

LightRE 是面向 ARM64 SO 的逆向分析工具，首要目标是恢复 VM 混淆中的真实业务逻辑，重点处理其中的控制流平坦化和 MBA（混合布尔算术）混淆。以 Agent Skill 为入口，结合 Ghidra 和 Codex，目标是交付可编译、可独立运行、可继续维护的等价源码工程。静态调查、unidbg 动态实验、表达式化简与差分验证的联合方案见 [核心分析路线](docs/LLM_DECOMPILER_DESIGN.md#vm控制流平坦化与-mba)；动态与求解能力尚未接入。

当前 V0.2 范围为 Ghidra 直接反编译、一轮 LLM 保守整理与 Web 评论续做。允许保留平坦化、goto、MBA 和 Ghidra 类型，去混淆及 VM 语义恢复留到后续版本；新任务不自动追加深入分析。V0.2 已于 2026-11-01 通过用户人工验收；DG 核心巨函数尚无 C 候选，按用户决定留到后续差分阶段处理。范围与实跑记录见 [V0.2 交付记录](docs/milestones/v0.2/DELIVERY.md)。

已有工作区也可用 `--input-mode assembly` 直接让模型从汇编生成候选，跳过 Ghidra 伪代码及初稿；全部已保存指令和地址一次性内嵌到初始提示词，保留机器码及知识文件快照，可用原 ELF 和 objdump 补核。用法见 [纯汇编输入](docs/milestones/v0.2/USAGE.md#纯汇编输入)。

默认首轮及 Web“继续还原”也会将既无 Ghidra 初稿也无已有候选的函数自动分流到完整汇编输入，保留读取工具并按每批 600 秒执行。汇编与伪代码整理分别合批；超大单函数仍独占一批，不需手动补发纯汇编任务。

长期自动化以 [Goal Loop](docs/GOAL_LOOP.md) 为支柱：一次提交目标，由 Python 循环调用 `codex exec`，根据实际构建、运行与回归结果自动续做，直至通过交付检查。该闭环尚在规划；现有功能提供静态分析、源码候选与可选的 Web 审阅。

下一小步 [V0.31](docs/milestones/v0.31/PLAN.md) 先做简单 Android ARM64 测试 SO 的 unidbg harness、结果保存与重放，以及每轮独立 exec、完整保存返回和反馈真实失败的 harness 修复 loop，尚未实现。真实目标适配、候选构建和双边差分按 [V0.3 总体方案](docs/milestones/v0.3/PLAN.md) 后续分步加入。

让 Codex 读取项目中的 [SKILL.md](SKILL.md)，描述目标目录或二进制文件即可，无需全局安装 Skill：

```text
读取 LightRE/SKILL.md，打开 ./dg-samples/dC6BDAE，展示函数和汇编。
```

Agent 会识别目标、创建或恢复外部分析工作区、导出结果，启动 Web 并返回访问地址。默认工作区位于调用 Skill 时的目录下，相对路径也以该目录为基准；临时切到工具目录不改变分析产物位置。上例假设 LightRE 与 dg-samples 位于调用目录下，其他布局使用实际路径。也可使用仓库内的启动指令调用 Codex CLI：

```bash
codex exec --sandbox danger-full-access --skip-git-repo-check - < LightRE/tests/dg_start.md
```

仓库内置 ARM64 minilua 样本，可将 [minilua 启动指令](tests/minilua_start.md)交给 Agent 体验；已有样本可使用 [dg 启动指令](tests/dg_start.md)。

分析流程由 Ghidra Headless 统一导出自身识别的函数、范围、汇编、参考伪代码与分组。LightRE 自动整理模块初稿，再按模块分批调用本地 Codex CLI，默认 8 批并发。单函数反编译失败保留汇编和原因，首轮材料不足则明确未解决，新任务完成一轮后停止，也可用评论继续调查；静态导出整体失败则保留旧结果并报错。首轮直接批量审阅初稿并返回局部修改，避免逐函数深入调查和重复输出大段源码；失败独立有限重试，进度区分候选、未解决与执行失败。Web 读取已保存结果，不重复启动 Ghidra。Rizin 仅作为 Agent 按需使用的可选工具。

Web 支持函数名称/RVA 搜索、汇编分段浏览、源码查看及并列对照、状态筛选和任务进度。用户可针对函数提交修改建议继续分析，无需逐个认可；评论优先入队，任务支持暂停、继续和取消。Web 展示实际保存片段的代码和普通注释，隐藏机器定位注释；提交建议保留原始片段快照与摘要。候选均未经执行验证，unidbg 和差分验证尚未接入。

分析工作区与工具及样本仓库分离，位置可指定。每份 SO 独立保存 SQLite、源码、知识和任务记录；源码位于 `source/src/`，函数知识统一保存在 `knowledge.md`，按 RVA 分节。任务、模型输出和日志持久保存，新会话可继续处理未解决问题。

- [使用说明与本地 API](docs/milestones/v0.2/USAGE.md)
- [文档入口：职责、阅读顺序与版本范围](docs/README.md)
