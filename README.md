

### LightRE

LightRE 计划以 Agent Skill 为主要入口，用户向 Codex 描述目录和目标：

```text
用 LightRE 打开 ../dg-samples/dC6BDAE，展示函数和汇编。
```

V0.1 以这句聊天作为启动入口：Agent 识别目标 SO、导出分析结果、启动 Web 并返回访问地址，用户无需手写 Python 命令或端口参数。后续再按需加入反编译和动态验证。

分析 workspace 按用户指定或上下文动态创建在可见位置，与 LightRE 工具代码分开。多个 SO 版本各自保存 SQLite、源码、知识与证据，共享兼容的 harness；跨版本逐步总结公共 VM 知识，帮助后续版本分析，各版本结论仍需独立验证。

当前 workspace 放在会话工作目录 `/work2/` 下：`dC6BDAE-analysis/` 和 `minilua-analysis/`，不放入工具或样本 Git 仓库。Agent 启动快捷指令：[dg-samples SO](tests/dg_start.md)、[minilua](tests/minilua_start.md)；用于打开样本供人工查看，不运行测试套件。一个 Web 进程固定打开一份 SO 结果，换目标时由 Skill 重启服务。

核心是分析 VM/OLLVM 等复杂混淆的 SO：Agent 根据功能说明写脚本调用 LightRE 的本地 Python API，反复运行 unidbg、保存 Evidence 并继续分析。最终交付简单可读、可执行的等价源码，用于二次开发；Web 帮助人工查看逻辑、验收版本和提交后续任务。

Web 反馈后，Agent 继续按需调用 Rizin/Ghidra 和 unidbg，多轮分析、差分验证并更新结果。失败尝试和反例保留为 Evidence，未解决项及续做条件记入知识 Markdown，供新会话继续处理。

当前 V0.1 已实现：独立分析导出、外部工作区，以及只读 Web 的函数搜索、LINES 排序、浅色/深色、滚动自动加载和紧凑布局。左栏可在 220–400px 范围拖动调宽，三列随宽度调整，LINES 居中。让 Agent 读取本项目的 [SKILL.md](SKILL.md) 并打开目标即可，无需全局安装。本轮人工验收反馈与样本记录见 [V0.1 milestone](docs/milestones/v0.1/DELIVERY.md)。

```text
读取 /work2/LightRE/SKILL.md，打开 /work2/dg-samples/dC6BDAE，展示函数和汇编。
```

用法与本地 API 见 [V0.1 使用说明](docs/milestones/v0.1/USAGE.md)。动态分析和 LLM 还原留待后续小步实现。

下一步已确认 [V0.2：单函数还原候选展示](docs/milestones/v0.2/PLAN.md)，目前仅记录计划，尚未实现。

代码与文档按职责放置：`src/analyzers/rizin.py` 负责分析导出，`src/workspace.py` 提供工作区及持久结果读取，`src/webui/` 只展示结果。阶段使用说明与交付记录放在 `docs/milestones/<版本>/`；长期设计、需求和实现计划保留在 `docs/`。

继续开发时先读[当前交付记录](docs/milestones/v0.1/DELIVERY.md)、[设计文档的开发基线](docs/LLM_DECOMPILER_DESIGN.md#开发基线)和[小步计划](docs/IMPLEMENTATION_PLAN.md)，根据用户反馈确定下一个可运行的小增量。

[思考](docs/ARM64_RE_R&D.md)
[架构设计](docs/LLM_DECOMPILER_DESIGN.md)
[小步实现计划](docs/IMPLEMENTATION_PLAN.md)
[开发进展](docs/REQUIREMENTS.md)
