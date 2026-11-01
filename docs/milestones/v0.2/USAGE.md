# V0.2 使用说明

读取 [SKILL.md](../../../SKILL.md)，让 Agent 分析目标并打开 Web。两个 Markdown 只是人工启动快捷指令：[dg](../../../tests/dg_start.md)、[minilua](../../../tests/minilua_start.md)。

```text
读取 LightRE/SKILL.md，分析 LightRE/samples/minilua/deliverable/minilua，并打开 Web。
```

## 正常流程

V0.2 已于 2026-11-01 通过用户人工验收；完整实跑与剩余问题见 [DELIVERY.md](DELIVERY.md)。以下描述当前实现，不表示两个样本已经全部还原。

分析入口先完成 Ghidra Headless 自身枚举函数、范围、汇编、分组与参考伪代码 → LightRE 写入初稿并合并为模块 → 后台按模块循环分批调用 Codex CLI。无需交互 Agent 手动写模块或逐个发指令。初始分组依次使用 Ghidra 命名空间、导入/跳板、内存区块，缺失时用 recovered；去掉符号的 SO 往往没有原始源码模块，这些只是可追溯的初始分组。所有尚无 LLM 候选的函数都会进入处理，包括刚写入的 Ghidra 初稿。同模块按汇编指令数打包，每批最多 4,000 条指令、32 个函数，并限制参考代码与知识合计 60,000 字符（单个超大函数仍独立处理）；超过 4,000 条指令的函数独立排队，完整汇编仍保存在 workspace，按模块先处理短函数。Ghidra 的项目与结果持久化，版本、脚本、目标和导出超时配置未变时复用；不会为每个函数重启 Ghidra。单函数反编译失败时仍导出完整汇编与错误，首轮材料不足则保留未解决原因，新任务不自动追加深入去混淆任务；整体自动分析超时或导出失败不发布不完整目录。参考包含 image_base，LLM 必须将 Ghidra 的 VA 转成 RVA。函数列表、范围和 LINES 均来自同一次 Ghidra 分析，不再使用 Rizin 的函数清单。同一 SO 保持一个调度进程，任务内默认同时运行 8 批 Codex；每批完成即发布，不等待最慢批次。主进程统一写入结果与进度，Web 可以边看边生成。

Web 顶部区分已有源码数量、LLM 候选数量和任务阶段；“暂停”保留已发布结果和排队请求，“继续还原”处理尚无 LLM 候选的函数（包括上轮未解决的函数）。全部已有 LLM 候选时不再启动。有初稿的首轮批次直接使用准备好的上下文，使用 low 推理强度，并关闭 shell、插件、apps 和子 Agent 工具；沿用本机模型与 provider。这是有初稿首轮批次的单次调用配置，评论/定向任务保留本机推理配置及工具调查。短函数（≤32 条指令）附完整汇编；默认首轮既无 Ghidra 初稿也无已有候选时，在同一任务内自动分流到完整汇编输入，保存全部指令快照并保留读取工具及本机推理配置；汇编与伪代码分别合批，超大单函数独占一批，不再只给首尾摘录。首轮优先返回 old/new 原文修改，LightRE 在不可变的 batch-*.input.json 快照上依次替换；old 必须非空且能匹配，错误项进入有限重试，其他有效项先发布。edits=[] 表示模型明确审阅后保留初稿，必须附观察和疑点；这不表示已经去除混淆。需要重写时仍可返回完整 code；code 与 edits 都为 null 才是模型未解决。评论/定向任务保留深入工具调查。有初稿的快速首轮每批 180 秒，汇编及评论/定向批次每批 600 秒；CLI 错误、超时、缺项或无效结果仅对缺失函数额外重试最多 1 次，间隔 5 秒，不取消其他健康批次。非限流失败拆小重试批次；429 将后续有效并发减半，最低 1，已运行批次继续。耗尽重试单列执行失败，不冒充模型未解决结论。

左侧 NAME 前显示处理状态：`✓` 为已有 LLM 候选（未经执行验证），`?` 为有分析记录但尚无 LLM 候选，`!` 为执行失败、尚无模型结果，`◌` 为正在处理，`↥` 为有排队任务，`G` 为仅有 Ghidra 初稿，`·` 为尚无源码。搜索框下可按这些状态筛选，可与名称/RVA 搜索组合；每 3 秒更新，保留当前选中函数、列表滚动和评论草稿。标记表示当前产物与进度，不代表执行验证通过，也不统计精确分析轮数。状态根据当前源码生产者标记、知识章节和任务记录推导；知识章节或候选文件存在不单独证明发生了真实模型调用，首轮验收须核对任务输出。

函数视图使用“汇编”“源码”“分栏”图标与短文字按钮，整组无外框，工具栏高 28px；选中项以浅背景及文字颜色突出，悬停可查看完整说明。分栏模式始终左侧显示汇编、右侧显示源码，中间分隔条可拖动调整比例（20%–80%），双击恢复均分，聚焦后支持左右方向键微调；浏览器记住两栏比例。两栏各自独立滚动，窄窗口也保持左右布局，内容超宽时在各栏内横向滚动；汇编仍按需分页，修改建议位于源码栏底部。浏览器记住所选模式，URL 的 `view=assembly/candidate/split` 可指定视图；切换函数保留模式。

选中函数，打开“源码”或“分栏”：

- 查看当前模块文件中该函数的代码正文和普通注释，支持 C/C++ 语法高亮并适配深浅主题；高亮资源随工具提供，无需联网。机器定位及生产者注释默认隐藏，开头空白行仅在展示时省略，文件原文和建议提交摘要不变；“分析上下文 / 未解决问题”可展开。
- Ghidra 初稿显示“尚未经过 LLM”，不能当作 LLM 已处理。未解决时保留初稿与原因。
- 无需逐函数人工认可；没有修改建议时自动任务继续推进，源码正确性由编译、测试与行为验证判断。
- 在输入框写具体修改建议，点击“提交修改建议”。评论保存为本地任务并排队进行该函数的下一轮分析。当前 SO 忙时也能入队。相同活动请求去重，不同评论分别保留，执行时读取最新候选；提交时页面已过期仍需刷新，草稿保留。
- 建议提交时核对当前源码片段的 SHA256（不是原始 SO 的 SHA256）；旧页面提交时若文件已变化会拒绝。重新点击“源码”读取当前文件，确认建议仍适用后再提交。
- 后台进度每 3 秒读取；结果变化后自动更新当前候选。有评论草稿时暂不自动替换，避免打断审阅。切换函数会保留本页面内的评论草稿。

仅打开已有静态结果不创建分析任务；已有待执行队列时，启动 Web 会恢复调度。暂停状态会保留，需点“继续还原”。该按钮先恢复既有任务；队列无活动任务时新建一次处理尚无 LLM 候选函数的首轮，包括此前未解决或执行失败的函数，不是持续自动重试。关闭 Web 不等于暂停队列。用户明确只浏览、不执行任务时，先暂停该 SO 队列。换 SO 由 Skill 重启 Web，不提供工作区切换。

## Agent 入口

Skill 以开始执行时的 cwd 作为固定调用目录，未指定 workspace 时使用 `<调用目录>/<名称>-analysis`。用户输入的相对路径均据此解析，先转成绝对路径再传给分析和 Web；安装依赖或临时切到 LightRE 目录不会改变产物位置。工具及样本 Git 仓库仍不用于存放分析产物。

环境为 Linux / Ubuntu 24 容器，Python 3.10+。首次导出需要 Ghidra 及其版本要求的 JDK；Rizin 为可选查询工具，可用 install_deps.sh --with-rizin 安装；还原需要已安装、登录的本地 Codex CLI，沿用现有模型和 provider 配置。Web 与知识维护使用 requirements 中依赖；`markdown-it-py` 同时用于分析进程与 Web。系统 Python 的依赖由 install_deps.sh 安装 python3-markdown-it，虚拟环境用 pip install -r requirements.txt。

先准备一个依赖完整的 Python 环境，再执行分析；分析入口、worker 和 Web 使用同一解释器。下面是首次环境准备及启动示例，已有环境只补缺失依赖，不重复创建或安装：

```bash
python3 -m venv <LightRE>/tools/venv
<LightRE>/tools/venv/bin/python -m pip install -r <LightRE>/requirements.txt
bash <LightRE>/scripts/install_deps.sh
<LightRE>/tools/venv/bin/python <LightRE>/scripts/analyze.py <绝对目标路径> --workspace <绝对外部workspace>
<LightRE>/tools/venv/bin/python <LightRE>/scripts/web.py <绝对SO分析目录> --host 0.0.0.0 --port 8000
```

Ubuntu 缺少 venv 时安装 `python3-venv`。仅浏览跳过安装 Ghidra 和分析入口，只准备 Python 后启动 Web；不需要 source `set_env.sh`，该文件会在本地 Ghidra 缺失时自动安装依赖。用户明确不需要 Web 时，只运行分析入口。

分析入口会打印 SO 目录及后台任务状态。已有静态结果和候选会复用。已有相同 SO 的队列运行时直接读取保存结果与进度，不重复执行分析入口；当前入口在 worker 持锁时会拒绝再次导入。`--static-only` 执行静态导出与模块初稿生成，不新建 LLM 任务，也不主动停止已有队列；重复 `--rva 0x23314` 可限定本次还原函数（包括已有候选），用于定向分析，不作为默认流程。`--force` 重新导出静态索引，不是强制重跑全部 LLM。重导出前先暂停该 SO 的活跃队列并等待 worker 退出。未变函数的源码保留；范围变化或不再独立成函数的旧代码及 SQLite（含旧版评价记录，如有）保存到 history/，边界关系保存在 references/ghidra/boundaries.json，关联知识仍可读取。

Codex 非交互命令是 `codex exec`，`-p` 表示配置 profile。可选环境变量：

- `GHIDRA_HOME`：可选 Ghidra 安装目录，默认 `<LightRE>/tools/ghidra`。
- `LIGHTRE_GHIDRA_ANALYSIS_TIMEOUT` / `LIGHTRE_GHIDRA_FUNCTION_TIMEOUT`：参考分析与单函数反编译时限，默认 300 / 60 秒。
- `LIGHTRE_CODEX_WORKERS`：同时运行的批次数，默认 `8`；分析入口可用 `--jobs N` 为新任务覆盖，`1` 为串行。复用已有活动任务不会修改其 workers，429 降低的 effective_workers 会随该任务保存。
- `LIGHTRE_CODEX_BIN`：Codex 可执行文件，默认从 PATH 找 `codex`。
- `LIGHTRE_CODEX_PROFILE`：现有 Codex profile 名称，默认不指定。
- `LIGHTRE_CODEX_SANDBOX`：默认 `danger-full-access`，使用当前容器的执行权限，适用于不能创建 sandbox namespace 的容器；允许 namespace 的环境可设为 `read-only`。提示要求只读调查，候选由 LightRE 统一写入。

调用以参数列表和 stdin 传入，不将评论拼接为 shell 命令。Agent 不使用 MCP，直接读取持久汇编并按需调用本地工具。当前流程不执行 unidbg。安装脚本检查所装 Ghidra 的 Java 要求，Ubuntu 24 使用 JDK 25；ARM64 容器缺少本机 decompile/sleigh 时从发行包源码构建。

## 纯汇编输入

已有导出的 SO 工作区可直接给模型汇编，不依赖 Ghidra 伪代码：

```bash
<LightRE>/tools/venv/bin/python <LightRE>/scripts/analyze.py \
  <绝对SO分析目录> --input-mode assembly --rva 0x1c07c --jobs 1
```

此模式 target 是已导出工作区目录，不是原始 SO；不接受静态导出参数 `--workspace/--name/--force/--static-only`。省略 `--rva` 时选择尚无 LLM 候选的函数，已有 LLM 候选默认复用；显式指定 RVA 可重做该函数。该模式跳过 Ghidra 参考读取与模块初稿生成，不启动 Ghidra。函数目录及汇编仍复用已有静态结果，不承诺绕过新二进制导入。

每批任务生成 `batch-0000-0x0001c07c.asm`（含全部已保存指令、原始字节、范围及地址换算说明）和对应 `.knowledge.md` 快照。全部已保存汇编和知识直接内嵌在同一初始提示词，不只提供快照路径，模型无需先分页读文件。内嵌格式保留全部指令和地址，直接分支/调用目标转为 RVA，其他地址操作数保留原体系；原始机器码和操作数保留在 .asm 快照中。仅遇到具体疑点时才读取文件或用 objdump 补核。调用前检查 Codex 单条输入的 1,048,576 字符上限；超限时保存完整提示词、记录明确的执行失败，不截断输入、不调用模型，也不自动重试同一超限请求。模型侧的其他上下文错误仍按实际日志记录。已保存范围可能漏块，快照完整不意味着静态函数边界完整。模型禁止读取 Ghidra 伪代码或调用 Ghidra，允许保留 goto、平坦化和低层表达式，不要求本轮去混淆。

纯汇编模式保留 shell 工具及本机推理配置，禁用 plugins、apps 与子 Agent，每批 600 秒，输出完整 code 或 null 及 notes，不使用首轮局部修改协议。沿用队列、单任务取消、暂停恢复、失败有限重试和统一发布，任务保存 input_mode=assembly；与 reference 请求分别去重，新任务不自动追加深入分析。没有形成候选时仍记为模型未解决，不用占位代码凑覆盖。

内部调用：`start_task(reader, [0x1c07c], workers=1, input_mode='assembly')`。定向请求尊重现有暂停状态；需按用户意图用 `recovery.queue.resume(reader)` 恢复，不能重复提交任务推动。当前 Web 继续按钮使用默认 reference 请求，并自动将既无初稿也无已有候选的函数分流到 assembly 批次；显式强制纯汇编使用上述 CLI 或内部 API；Web 沿用已有队列、进度与源码展示，不需重启。

## 自动运行

批量还原是 LightRE 分析流程中的一步。通过 Skill 启动分析，或由自动化脚本调用同一个分析入口：

```bash
<LightRE>/tools/venv/bin/python <LightRE>/scripts/analyze.py <绝对目标路径> --workspace <绝对外部workspace>
```

入口完成 Ghidra 静态导出与模块初稿后，自动在后台执行 Codex 还原，随后退出；已有静态结果会复用，已有 LLM 候选会跳过，Ghidra 初稿仍会处理。关闭聊天、终端或 Web 不会停止后台任务，不需要人工逐个函数发起。进度和结果保存到 workspace，Web 负责查看及反馈。

不提供独立 recovery CLI。分析入口、Web 的继续按钮及评论流程共用内部 `src/recovery/` 能力和任务锁。每个任务处理指定范围一轮；新建任务只执行一轮，模型未解决项保留原因；评论仍可主动续做，旧任务显式启用的自动续做保留。调度器在队列清空后退出，新请求自动启动。评论优先，同优先级 FIFO；出现评论时，批量分析不再发新批次，待当前活跃批次完成后让位，之后从 done_rvas 继续。未解决函数保留原因，单批执行失败有限重试后保留失败，继续处理其他函数；存储和来源校验失败才中止任务。详细规则见 [TASK_QUEUE.md](TASK_QUEUE.md)。原生 Codex CLI 是工具内部的执行依赖。

首轮每批实际由 LightRE 调用的 Codex 命令形态如下（文件路径由工具生成）：

```bash
codex -a never exec --sandbox danger-full-access --skip-git-repo-check \
  -C <SO分析目录> --json --output-schema <batch.schema.json> \
  --disable shell_tool --disable plugins --disable apps --disable multi_agent \
  -c 'model_reasoning_effort="low"' \
  -o <batch.result.json> - < <batch.prompt.md> \
  > <batch.events.jsonl> 2> <batch.stderr.log>
```

实现使用 subprocess 参数列表及 stdin/stdout，不经过 shell 拼接。`--output-schema` 约束返回函数 RVA、完整候选或局部修改、上下文，模块位置由 LightRE 维护，LightRE 校验后保存。首轮的 schema 包含 rva、code、edits 和 notes；评论/定向任务返回完整 code 或 null 及 notes，不使用局部修改协议。Codex 命令或模型失败会记录在任务状态及日志中，不能将退出或文件存在视为逻辑已验证。调用依据为 OpenAI Docs：[非交互运行](https://developers.openai.com/codex/noninteractive/)和[配置参考](https://developers.openai.com/codex/config-reference/)。这些配置仅作用于本次首轮调用，不修改用户全局配置。

## 判断首轮是否结束

本版只要求 Ghidra 直接反编译加一轮 LLM 保守整理。允许保留平坦化状态机、goto、MBA、间接调用和 Ghidra 类型；现成初稿包含警告或缺失路径时可保留原文并说明局限，不要求本版解决去混淆或 VM 语义。没有可用初稿且材料不足时仍返回未解决，不能用占位代码凑覆盖。新建任务 auto_refine=false，结束后不自动追加深入任务。旧任务显式保存 auto_refine=true 时仍保留一次定向续做，通过 followup_id / parent_task 跟踪；它不递归创建任务，每批限时 600 秒。

先看具体任务的 `state.json`，不把入口退出或 queued/running 当作完成。`processed` / `done_rvas` 包含模型未解决、耗尽重试的执行失败以及被其他任务完成而跳过的函数；`generated` 是本任务实际发布的候选数，也包含模型明确保留初稿的情况。因此 `processed == total` 不能单独证明全部函数生成了新代码。

`complete` 表示本轮指定范围已有候选；`partial` 表示仍有模型未解决或执行失败。任务内 `unresolved` 也可能包含 `failed_rvas` 中的执行失败；统计模型未解决时要排除这些 RVA，不能将两者直接相加。执行失败不是模型结论。`GET /api/recovery` 顶层的 unresolved / failed 是按当前源码、知识和历史任务推导的当前目录视图，不能替代指定任务的失败记录，尤其是对已有候选的评论任务。`run_duration_seconds` 是最近一次 run_task 执行段耗时，包含该段等待和重试，不累计先前暂停前的执行段。验收报告写清静态结果与候选复用范围、实际处理量、耗时和缺口，必要时核对 batch 输出覆盖；不因知识文件存在就算完成。

当前首轮、评论提示和 JSON 协议在 `src/recovery/jobs.py`。`src/recovery/prompts/RECOVER_AND_REFINE.md` 是尚未接入的后续工程模板，不能用它直接替换本轮协议或原地改写已登记源码。

检查全量完成时，旧任务如有 `followup_id` 也应包含对应任务，保留父任务的历史结果，并按函数 RVA 核对当前候选及真实输出；不累加两轮的候选计数作为函数数。总耗时从首轮 started_at 到最后续做 finished_at 计算，包含中断和等待，并将静态导出单独报告。全部得到模型结果、全部有候选、全部逻辑已恢复是不同结论；模型未解决或执行失败必须如实列出，不把记录原因等同于达到全量还原目标。

## 持久目录

```text
<外部workspace>/<SO名称>-<SHA短码>/
  index.sqlite                 # 函数、汇编、派生源码索引
  binary/original.so
  references/ghidra/
    functions.json             # Ghidra 自身枚举的 RVA、名称、范围、行数、分组、伪代码与错误
    assembly.jsonl             # 同一次分析的逐函数汇编，导入 SQLite 后供 Web 读取
    boundaries.json            # 重新导出时旧边界的归属与归档路径
    state.json                 # 身份、工具/脚本指纹、完成状态
    headless.log
    project/                   # 一份 Ghidra 项目
  source/
    README.md
    .clang-format              # 本 SO 的源码排版配置
    src/
      text.c                   # 来自 Ghidra .text 分组，最多 20 个函数
      text_002.c
      imports.c                # 导入/跳板函数
      recovered.c              # 无 Ghidra 分组时的回退
  knowledge.md                 # 所有函数知识；每函数一个 RVA 二级标题
  .knowledge.lock              # 知识更新与旧文件迁移的互斥锁
  tasks/
    .lock                      # 同一 SO 执行互斥
    .queue.lock                # 提交和调度退出握手
    .dispatcher.lock           # 保证只有一个后台调度器
    .paused                    # 存在时暂停整个队列
    worker.log                 # 调度器日志
    <任务时间-ID>/
      state.json               # 此任务的状态、进度、done_rvas、批次、重试次数、失败项、耗时
      task.md                  # 任务目标、函数及原始评论
      reviewed.c               # 评论提交时的代码快照（若有）
      cancel                   # 取消请求标记（若有）
      batch-0000.input.json    # 首轮模型看到的源码正文快照，供修改校验和恢复
      batch-0000.prompt.md
      batch-0000.schema.json
      batch-0000.result.json
      batch-0000.events.jsonl
      batch-0000.stderr.log
```

每个函数只维护一份当前候选片段；上下文统一存入当前 SO 的 knowledge.md，以函数 RVA 为二级标题。源码不再一函数一个文件，可用 Ghidra 初稿先按分组写入模块，LLM 再替换自己的函数片段；超过 20 个函数时分成同模块的后续文件。已有函数更新保留模块位置，仅替换自己的片段，其他函数原文不变。旧单函数文件逐个迁移，核对正文一致后删除，不保留第二份当前源码。任务目录保留每批模型原始输出，未知原因也保留。新轮先读当前候选和上下文，不依赖对话内存。不同 SO 的结果隔离；此处没有共享知识库。SQLite 不存源码正文，`source_index(function_rva, path)` 表按需创建，无需迁移现有汇编数据。索引由源码的 origins 标记派生，文件变化时重建全部映射，删除旧行；手动移动/删除函数不会留下旧索引。

源码整理由 `src/recovery/source.py` 实现，属于分析流程本身；`merge_sources(reader, modules=None)` 也可通过内部 API 调用，按 RVA 分组及打包，每文件最多 20 个函数。整理保持函数内容摘要不变，保留手写共享声明和非 C 文件，重新建立路径索引；目录替换中断可恢复，不留下两份当前源码。

Ghidra 初稿和模型输出在写入候选前自动调用 `clang-format`，统一缩进、空格、参数换行与花括号位置。工具默认风格保存在仓库 `.clang-format`，首次使用复制到该 SO 的 `source/.clang-format`，已有配置保留，可独立调整。默认 4 空格、100 列、左花括号跟随声明；不排序 include、不重排 using 声明、不重排注释或拆分字符串。clang-format 18 会保留声明与 `{` 之间的空白行，因此格式化前另在词法保护下清理 `)` 与 `{` 之间的多余空行，跳过字符串和注释。`scripts/install_deps.sh` 同时安装 formatter。

格式化失败不丢弃候选原文：Ghidra 初稿的错误记在 `prepare_source` 返回值及任务 `source.format_errors`，模型输出的错误记入对应函数知识。已有模块可在后台任务空闲时通过 `from recovery import format_sources; format_sources(reader)` 批量格式化；工具先准备并检查全部结果，再逐文件原子写回，保留定位 ID、来源与生产者。与仅搬运文件的 `merge_sources` 不同，格式化会改变原始片段摘要，Web 应刷新后再提交建议。底层 `save_candidate` 仍按原文保存，旧标记迁移不隐式格式化。

候选文件使用简短的结构化定位注释，不重复保存 binary 文件名、原始 SO 的 SHA256 或验证状态：

```c
// @lightre.begin {"v":1,"id":"f00000004","origins":["0x4"]}

int read_count(void) { return 1; }
// @lightre.end {"id":"f00000004"}
```

编辑时保留模块头及成对的 JSON 标记；重命名和移动保留 ID。当前每段仅支持一个 origins RVA，多对多仍待实现。ID 和 RVA 都以所属 SO 分析目录为范围，跨 SO 引用/导出要另附原始 SO 身份；JSON 标记本身不携带 SO 哈希，不能检测手工从其他 SO 目录复制的同名片段。词法扫描跳过字符串和普通注释中的示例，损坏、重复或不匹配的标记会报错。旧标记仍可读取，保存该函数时转换；全部旧模块可在任务空闲时调用 `from recovery import migrate_source_markers; migrate_source_markers(reader)`，逐文件原子转换并核对正文。详细约定见 [源码定位设计](../../SOURCE_NAVIGATION.md)。

Web 使用 `read_candidate` 返回的 `display_code` 隐藏定位及开头的 Ghidra 生产者注释，普通注释和代码保持原文。`code` 与 `sha256` 仍表示文件中的原始片段，任务快照和建议提交使用该摘要；修改同模块其他函数或移动文件不会使建议失效，标记迁移会改变摘要，需刷新后提交。后台发布前仍检查外部修改。源码读取会按模块内容指纹重建索引；这不是统一发布 revision。页面自动更新由任务进度驱动，直接编辑文件后若进度没变，需主动重新读取或刷新页面。当前尚不保证全部候选拼在一起可编译，共享类型、外部接口和构建方式后续再整理。

## 内部 API 与函数知识

内部 Python API（将 `<LightRE>/src` 加入 `sys.path`）：

```python
from workspace import WorkspaceReader
from recovery import read_candidate, save_candidate, merge_sources
from recovery import read_knowledge, save_knowledge, save_knowledge_batch, knowledge_rvas, migrate_knowledge
from recovery.jobs import start_task, progress, stop_task

reader = WorkspaceReader('/外部workspace/目标结果')
assembly = reader.assembly(0x23314, offset=0, limit=200)
candidate = read_candidate(reader, 0x23314)
notes = read_knowledge(reader, 0x23314)
addresses = knowledge_rvas(reader)
state = progress(reader)  # 读取持久进度；不新建模型任务
```

显式调用 `start_task(reader)` 会恢复队列并排队首轮；它只使用已导出的 Ghidra 参考，不运行静态导出。新会话需要完整分析时使用 scripts/analyze.py，而非把这个 API 当成二进制导入入口。只浏览时不要调用 start_task。

`read_knowledge(reader, rva)` 没有对应记录时返回空字符串。`save_knowledge(reader, rva, notes)` 替换该函数的当前正文并返回 `knowledge.md` 路径，保留其他函数及非函数章节；更新前应先读取并保留仍有效的知识和人工备注。`save_knowledge_batch(reader, {rva: notes, ...})` 统一保存一批函数，至多原子写入一次，内容未变不重写；分析发布使用此接口。`knowledge_rvas(reader)` 返回已有知识章节的 RVA 集合。上述 API 无需启动 Web。

Ghidra 入口在 `src/analyzers/ghidra.py` 和 `ghidra_scripts/ExportReferences.java`；导出事务保留旧结果，边界变化的归档与恢复在 `src/analyzers/migration.py`；模块初始化与合并在 `src/recovery/source.py`；候选读写和源码索引在 `src/recovery/candidates.py`；队列与调度在 `queue.py`，批量执行与续做在 `jobs.py`；知识维护在 `knowledge.py`。不再提供独立 candidate CLI。

知识 Markdown 示例：

```markdown
# 函数知识

## 0x00023314

### 事实
已从汇编确认的行为。

### 未知项
尚待验证的分支和下一步。

## 0x00012438

### 事实
另一个函数的理解。
```

`read_knowledge` 返回对应函数的正文，Web 仍显示相同原文；LLM 按 RVA 调用接口，不需要加载整份知识。`save_knowledge` 自动把输入正文中的一级/二级标题调整到三级及以下，代码块内容保持原样。Markdown 结构通过 markdown-it-py 解析，支持代码块、引用和 Setext 标题；重复 RVA、未闭合代码块等会拒绝覆盖。同一进程按 RVA 在内存中复用正文与章节位置；每次读取只检查文件状态，未变时不读取正文、不重新解析、不打开文件锁。文件被修改、替换或删除时下次读取重新加载。Web、worker 和独立 Codex 脚本各有自己的内存，不共享可修改状态。并发保存使用线程锁、文件锁和原子替换；一批完成后统一保存，不依赖退出或 crash 时补写，也不增加 SQLite 知识副本。人工直接编辑前暂停队列，并保留函数的二级 RVA 标题。

旧 `knowledge/0x*.md` 在读取、保存、枚举知识或启动分析任务时自动迁移，校验后删除；可用 `migrate_knowledge(reader)` 显式维护，返回处理的旧文件数。迁移中断可续做；若发现新旧正文冲突则保留两者并报错，不默默丢弃知识。读取知识也可能触发旧文件迁移，知识正文仍只保存一份当前内容；任务目录中的模型原始输出和日志继续保留。

HTTP：`GET /api/recovery` 读取进度及队列，`POST /api/recovery` 继续，`POST /api/recovery/stop` 暂停队列，`POST /api/tasks/{id}/cancel` 取消单个任务；`GET /api/functions/{rva}/candidate` 返回正文、内容摘要和上下文；`POST /api/functions/{rva}/feedback` 提交 `expected_sha256` 和非空 `comment`，创建修改建议任务，不保存认可或待评价状态。

开发快检：`python3 -m unittest discover -s tests -v`。全部使用临时数据和模拟模型输出，不运行真实工具、Web 或浏览器。界面留给人工验收。
