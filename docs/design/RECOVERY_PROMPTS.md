# Recovery Prompt 与内部 Skill

Recovery 只有两个任务入口：让 LLM 改善函数代码，以及根据用户意见修改。运行时指令共 **4 份 Markdown**，由 Recovery 自己维护并随包发布。

| 文档 | 调用位置 | 职责 |
| --- | --- | --- |
| [RECOVER.md](../../src/recovery/prompts/RECOVER.md) | `jobs._start_batch()`，无用户评论 | 首轮或指定函数重跑；内嵌当前 C、知识及必要汇编，要求改善命名、类型、伪语法和控制流 |
| [FEEDBACK.md](../../src/recovery/prompts/FEEDBACK.md) | 同上，`mode=comment` | 内嵌用户意见、执行时源码及知识；优先返回局部补丁、整合后的知识和独立回复 |
| [lightre-recovery/SKILL.md](../../src/recovery/skills/lightre-recovery/SKILL.md) | `skills.snapshot_skill()` 保存快照，两个任务模板注入其名称、描述和路径 | 集中维护命名、类型、改名绑定检查、控制流、知识整合和取证方法，由内部 Codex CLI 读取 |
| [references/ASSEMBLY.md](../../src/recovery/skills/lightre-recovery/references/ASSEMBLY.md) | Skill 在遇到具体机器证据疑点时引导读取 | ELF/VA/RVA 换算、缺失块、反编译失败及特殊 ABI 核对 |

Skill 只供 LightRE 自己启动的 Codex CLI 使用。程序从安装位置读取资源，为每批复制入口和参考文档并记录 SHA256；不创建全局 Skill、不安装插件、不依赖原生 Skill 注册。任务范围、只读限制、输入模式与 JSON schema 由任务 Prompt 和程序控制，Skill 提供具体处理方法。

## 两个任务入口与输入模式

`queue.submit()` 仍用 initial、targeted、comment 记录调度语义：首轮自动排除已有 LLM 候选，指定 RVA 可重新分析，评论优先处理。但 initial 和 targeted 使用同一份 `RECOVER.md`、相同的内嵌代码准备与补丁发布流程，不再各有一套提示词。

更新 Prompt 不会自动重跑已完成的 workspace。Web 函数标题旁的“重新分析”调用 `POST /api/functions/{rva}/recover`，显式创建 targeted 任务，即使已有 LLM 候选也不会被首轮跳过规则过滤；原代码和知识继续作为基线。“继续还原”仍只负责原队列及尚未处理函数。实际使用的指令以该批 `.prompt.md` 为准，不能用当前安装的模板推断历史任务已使用新规则。

reference 输入提供执行时当前 C；没有当前 C 时使用 Ghidra 参考初稿。先改善已有代码，有具体疑点再只读核对汇编。参数和局部变量重命名须更新同一绑定的声明与所有引用，保持调用、副作用、访问宽度及 ABI；保留未知路径与不确定性。

assembly 只是这两个模板中的 Jinja 条件分支：输入全部已保存汇编与知识，禁止读取旧 C 或 Ghidra 伪代码，返回完整 code 和 notes，补丁字段为 null。普通评论也走 `FEEDBACK.md`，不再追加单独的汇编评论文档。默认首轮对没有初稿也没有当前 C 的函数自动选择该输入分支；`assembly.py` 只准备数据和原始快照，不生成指令正文。

新任务不自动追加深入分析。旧任务显式保存 `auto_refine=true` 时继续支持原续跑策略；parent_task 作为数据交给 `RECOVER.md` 的短条件段，未另设 FOLLOWUP 模板。

## 任务记录与发布

- `state.json` 保存目标 RVA、模式、用户意见、提交时摘要、父任务、状态和计数，不再另生成 `task.md`。
- `reviewed.c` 保留评论提交时源码，仅用于理解用户当时看到的内容；执行时当前源码是修改基线。
- `batch-NNNN.prompt.md` 是本批完整输入；`.schema.json`、`.result.json`、`.events.jsonl`、`.stderr.log` 保存协议、输出和执行记录。
- `.skills/` 保存单个 Skill 及其引用，`.skills.json` 保持版本 1 的清单结构；实际读取情况看 events 日志。
- `.input.json` 保存精确 C 补丁基线；`.before.json` 保存执行前源码、路径、摘要和知识；assembly 另保存逐函数 `.asm` / `.knowledge.md`。
- `.changes.json` 保存逐 RVA 的差异、前后名称及 changed/unchanged/unresolved/conflict。`generated` 表示可用候选数，`changed` / `unchanged` 区分实际代码 token 改动与保留；格式化、空白、注释修改不计为代码改进。

模型只返回 JSON，不直接写工作区。Python 继续负责队列、锁、并发、取消、超时/重试、补丁验证、源码/知识冲突检查与发布。旧任务已保存的输入、结果和日志继续保留，续跑沿用既有兼容协议，不批量改写历史产物。

函数身份由 RVA/源码 ID 保存，函数名可以改进。Web 列表和搜索显示当前源码名，同时保留原始 catalog 名称。任务所有批次结束后，`synchronize_calls()` 更新能唯一绑定的直接调用并复核新名称仍指向同一 RVA，结果保存到 `renames.json`。局部遮蔽、宏、歧义或无法解析时保持原文并记录原因；函数指针、头文件原型等仍需核对。候选及 token 变化不构成可编译或语义等价证明。

## 合并与删除

原 INITIAL / TARGETED / ASSEMBLY 合入 RECOVER，汇编评论合入 FEEDBACK；三个 Skill 合为一个，两份机器取证参考合为一份。TASK、SKILLS、FOLLOWUP、汇编头/正文片段模板均已移除，任务和汇编材料直接由两个模板承载。

知识文件头仅包含 Binary、SHA256 等数据，由 Python 格式化；源码布局说明集中在 [USAGE.md](../USAGE.md)，不再生成额外的 source/README.md。已有 workspace 中的说明文档保留，不作为新任务的必读指令。

递归打包规则不变。常规测试覆盖两类任务、两种输入、完整汇编、改名与发布、冲突及中断恢复；真实 CLI 与 wheel/sdist 验证命令见 [使用指南](../USAGE.md#recovery-内部-skill)。
