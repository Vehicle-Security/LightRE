---
name: lightre
description: 分析 ARM64 SO 或打开已有 LightRE 工作区，导出静态结果、生成还原候选，并启动 Web 查看源码与提交修改建议。
---

# LightRE

这是随项目保存的 Agent 使用说明，读取即可使用，无需全局安装。先读取本目录的 [AGENTS.md](AGENTS.md)。当前为 V0.2，已于 2026-11-01 通过用户人工验收：支持静态导出、一轮 LLM 保守整理、Web 审阅和评论续做。允许保留混淆结构及已注明的 Ghidra 局限，去混淆与 VM 语义恢复留到后续版本。动态运行、差分验证及工程 Goal Loop 尚未接入，不把候选称为已验证源码。

## 1. 固定路径并确定操作

将调用此 Skill 时的 cwd 记为“调用目录”，在切换目录、安装依赖或启动子进程前固定。用户的相对输入、workspace 和已有结果目录都相对此目录解析，再转为绝对路径传给脚本。`<LightRE>` 是当前 SKILL.md 所在目录，不是调用目录，也不从样本位置或本机固定路径推断调用目录。

- **分析 SO/输入目录**：静态导出后默认启动全部尚无 LLM 候选函数的首轮分析，并打开 Web；用户明确无需界面时只运行分析流程。
- **只看汇编、不调用 LLM**：分析入口加 `--static-only`；这仍会导出 Ghidra 伪代码及模块初稿。已有后台任务不会被该参数停止；用户明确要求不运行分析时，还需暂停已有队列。
- **打开已有 workspace**：直接浏览保存的结果，不要求原始 SO 或 Ghidra 存在，不重新导出、不创建新任务。Web 启动会恢复已有且未暂停的排队任务；用户明确只浏览、不执行任务时，先通过 `recovery.queue.pause(reader)` 暂停队列。

文件输入支持 `.so` 和无扩展名的 ARM64 ELF。目录输入优先检查直接包含的 `.so`，跳过含 `index.sqlite` 的生成目录；多个无法区分的目标才询问。只处理选定输入，不自动导入其他版本、重建或修改样本。

## 2. 选择外部 workspace

尊重用户指定位置。未指定时使用 `<调用目录>/<名称>-analysis`：`.so` 取所在目录名，其他二进制取文件名去掉扩展名。例如 `./samples/example/libexample.so` 对应 `<调用目录>/example-analysis`，`./samples/minilua/deliverable/minilua` 对应 `<调用目录>/minilua-analysis`。

分析命令始终显式传入绝对 `--workspace`。重复请求复用同一位置和相同 SO 的已保存结果，保留其他版本；不为测试、重开或每次调用创建额外场景 workspace。产物不得放进 LightRE 或样本 Git 仓库，入口会拒绝这些位置；调用目录在仓库内时改用仓库外的可见位置，并说明原因。创建前说明路径，不重复请求确认。

外层 workspace 可有多个 SO 版本；每个 SO 分析子目录包含自己的 `index.sqlite`、源码、知识和任务。仅打开外层目录时，只有一个 SO 结果可自动选中，多个版本则明确目标。

## 3. 先准备 Python，再执行分析

选择一个 Python 3.10+ 解释器，同时用于分析入口、后台 worker 和 Web，避免依赖装在不同环境。优先复用 `<LightRE>/tools/venv/bin/python`；没有可用环境时创建 venv 并安装依赖：

```bash
python3 -m venv <LightRE>/tools/venv
<LightRE>/tools/venv/bin/python -m pip install -r <LightRE>/requirements.txt
```

已有环境先检查所需导入，依赖齐全不重复安装。Ubuntu 缺少 venv 时安装 `python3-venv`。`markdown-it-py` 在分析和知识读写阶段就需要，不能等启动 Web 时才准备。

只有静态导出才需要 Ghidra Headless 和匹配的 Java。优先使用 `<LightRE>/tools/ghidra`，也支持 `GHIDRA_HOME` 或 PATH；缺失时执行 `bash <LightRE>/scripts/install_deps.sh`。脚本支持 Ubuntu 24 容器，并为 ARM64 Linux 准备本机反编译器，同时安装 `clang-format`，供初稿和模型候选在发布前统一排版；风格保存在 SO 的 `source/.clang-format`。已有源码的批量格式化 API 及失败记录见 [使用说明](docs/milestones/v0.2/USAGE.md)。Rizin 是按需查询工具，需要时用 `--with-rizin` 安装，不参与默认目录导出。仅浏览已有结果不安装 Ghidra，也不必 source 会自动安装它的 `set_env.sh`。

默认还原需要本机已安装、已登录的 `codex`。在选定 Python 环境中执行：

```bash
<LightRE>/tools/venv/bin/python <LightRE>/scripts/analyze.py <绝对输入路径> --workspace <绝对workspace路径>
```

等待静态导出成功，使用入口返回的 SO 分析目录；后台模型任务随后自行运行。相同 SO、工具及导出配置的完整结果会复用，导出配置包括 Ghidra 超时设置。已有相同 SHA256 的结果且队列活跃时，直接读取进度和打开 Web，不再次运行分析入口；当前入口在 worker 持锁时会拒绝导入，即使静态结果可以复用。只在用户要求重新静态分析时使用 `--force`；重导出前暂停该 SO 的活跃队列并等待退出。失败时报告错误和日志，保留静态结果，不把旧结果或 queued/running 说成本轮完成。

## 4. 打开或复用 Web

每个 Web 进程只展示一个 SO 分析目录，没有页面内 workspace 切换。已有服务时请求 `/api/functions`，用返回的 `sample.sha256` 和 `sample.directory` 核对目标；相同则复用。需更换目标时，确认旧进程是本项目的 `scripts/web.py` 再停止、重启；无关服务占用端口则换空闲端口。

```bash
<LightRE>/tools/venv/bin/python <LightRE>/scripts/web.py <绝对SO分析目录> --host 0.0.0.0 --port <空闲端口>
```

以独立后台进程启动，日志保存在外部 workspace，例如 `web.log`。简单请求首页和函数目录确认可用，返回实际首页 URL、workspace 和 SO 分析目录。容器外访问需要端口映射，不能把容器内检查说成外部已可达。日常启动不运行测试套件、浏览器或整套 Web 测试。

打开 Web 可以在首轮完成前交付，并如实报告仍在运行。用户要求跑完全量或检查首轮验收时，跟踪原任务至终态；旧任务如有 `followup_id`，也跟踪对应续做任务；检查实际 batch 输出，报告静态导出与模型处理耗时、复用范围、候选、未解决及执行失败数。`processed == total` 或任务 `complete` 均不能证明完整逻辑已恢复；已知初稿截断、缺核心副作用等问题即使有候选也应报告。不要重复提交同一任务推动进度，也不要把“全部已尝试”当作“全部已还原”。检查文档或源码不启动模型、分析工具或 Web；用户停止 Web 后，等待新的启动请求。

## 5. 首轮、反馈和持久结果

默认首轮（包括 Web“继续还原”）对既无 Ghidra 初稿也无已有候选的函数，直接在当前任务中自动使用完整汇编输入。汇编批次与伪代码批次分开，保留 shell 及本机推理配置，每批 600 秒；超大单函数不拆分。这不是首轮结束后的深入续做，不需手动切换模式；任务顶层 input_mode 仍可为 reference，实际模式和限时以 state.json 的各批次 input_mode / timeout 为准。

用户要求模型直接从汇编还原时，对已有 SO 工作区使用 `scripts/analyze.py <绝对SO分析目录> --input-mode assembly --rva <RVA>`（可重复 RVA；省略则处理尚无 LLM 候选的函数）。该路径不调用 Ghidra、不读取参考伪代码或写初稿；将全部已保存指令和知识一次性内嵌到初始提示词，同时保存原始机器码和知识快照。模型无需先分页读取汇编，仅遇到具体疑点时用 shell/Python/objdump 补核原 ELF。每批 600 秒，沿用失败有限重试与任务队列；不自动深入续做。工作区若已暂停，定向任务会排队，按用户本次意图使用队列 API 恢复执行，不重复提交任务。静态目录和汇编复用已有导出；新二进制仍需原有静态导入流程。细节见 [纯汇编输入](docs/milestones/v0.2/USAGE.md#纯汇编输入)。

首轮由工具自动分批调用 `codex exec`，不是 `codex -p <提示词>`；`-p` 是 profile。默认 8 批并发，同模块及输入模式打包，优先短函数；有初稿的批次直接提供初稿、当前候选和函数知识，关闭调查工具，使用 low 推理，优先返回局部修改。无初稿的汇编批次使用上面的工具和限时配置。单批失败隔离、超时和有限重试由工具处理，不依赖聊天推动。精确预算、配置和恢复规则统一见 [使用说明](docs/milestones/v0.2/USAGE.md)与[任务队列](docs/milestones/v0.2/TASK_QUEUE.md)。

LLM 可以明确审阅后保留初稿、混淆结构和 Ghidra 警告并记录疑点，这属于已处理候选，不表示已去混淆或语义完整；没有可用伪代码且材料不足时保留未解决原因，不编造空函数或返回零。新建任务仅执行一轮，auto_refine=false，不自动追加深入分析；旧任务已保存 auto_refine=true 时仍按原策略恢复，以 followup_id / parent_task 关联一次定向续做，不递归追加。评论和定向任务仍可由用户主动发起，尚无自动构建与差分闭环。当前工程提示模板和后续计划不自动替代 V0.2 的 JSON 批处理协议。

源码只有 `source/src/*.c` 一份当前正文，LightRE 自动按 Ghidra 分组整理模块，不让模型搬文件。Web 展示同一片段的代码和普通注释，隐藏机器定位注释；接口的 `code` 和 `sha256`、评论快照仍使用原始片段。当前源码 ID 只在所属 SO 内唯一，每段仅对应一个原始 RVA；多对多、函数合并/拆分和统一发布 revision 尚未实现，见[源码定位设计](docs/SOURCE_NAVIGATION.md)。SQLite 的 `source_index` 是可重建映射，不复制正文。

每份 SO 使用一个 `knowledge.md`，按函数 RVA 二级标题分节；通过 `from recovery import read_knowledge, save_knowledge, save_knowledge_batch` 按函数读写，保留事实、假设、冲突和未知项。日志、模型输出和进度保存在 `tasks/<id>/`；新会话可以继续，不依赖 Agent 自己的内存。具体 API 和迁移方式见使用说明。

Web 支持函数搜索、LINES 排序、状态筛选、明暗主题、自动追加汇编以及源码并列对照。用户提交修改建议会保存 `task.md`、原代码快照并优先入队；无需逐函数人工认可，同一 SO 的不同轮次不并发改写。没有反馈时任务继续推进。关闭 Web 不等于暂停队列；暂停、继续和取消使用任务控制。

V0.2 是否通过由用户人工验收决定，标准见 [PLAN.md](docs/milestones/v0.2/PLAN.md)，实跑记录见 [DELIVERY.md](docs/milestones/v0.2/DELIVERY.md)。后续 Goal Loop、unidbg 与差分属于规划，不顺手进入下一版本。
