# 当前功能说明

本文是 LightRE 唯一的当前版本功能、操作与 API 说明，不再维护按版本划分的 USAGE。版本目录只保存范围、验收条件和实测证据。

产品目标是 LightRE 常驻服务器管理多个 Codex CLI 工作实例，并通过一个持久指导会话提供用户聊天窗口。该服务与交互整合尚待实施，见[目标架构](design/ARCHITECTURE.md#常驻服务与指导会话待实现)和 [V0.31 计划](milestones/v0.31/PLAN.md#维护与优化计划)。下文描述当前可用 CLI、源码 Web 和独立 agents 能力；内部 prompt/workflow 用作工作指令，外部 Codex 加载项目 Skill 只作为旧入口兼容。

| 能力 | 当前状态 |
| --- | --- |
| Ghidra 静态导出、一轮 LLM 候选、纯汇编模式、Web 评论队列 | V0.2 已于 2026-11-01 通过用户人工验收 |
| ARM64 Android SO 的 harness 运行、契约校验、重放与修复循环 | V0.31 开发功能，待人工验收；DG/minilua 单边运行已有真实记录 |
| 候选 SO 构建、双边差分、VM 语义恢复、完整工程 Goal Loop | 尚未交付，见 [路线图](ROADMAP.md) |

现有兼容调用方式仍可读取 [SKILL.md](../SKILL.md) 后向外部 Agent 提交目标：

```text
读取 LightRE/SKILL.md，分析 <目标 SO 或已有分析目录>，workspace 使用 <外部目录>，并打开 Web。
```

人工启动快捷指令：[DG](../tests/dg_start.md)、[minilua](../tests/minilua_start.md)。它们只指定样本，不会自动完成验收。下文命令中的 `<LightRE>` 是工具仓库绝对路径，`<SO目录>` 是包含 index.sqlite 的单个分析子目录。系统依赖统一通过 `scripts/install_deps.sh` 安装。

## 静态恢复与 Web

V0.2 已于 2026-11-01 通过用户人工验收；完整实跑与剩余问题见 [静态恢复交付记录](milestones/v0.2/DELIVERY.md)。以下为当前静态恢复流程；候选尚未经过动态等价验证。

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

环境为 Linux / Ubuntu 24 容器，Python 3.10+；仓库开发默认选择 `.python-version` 中的 3.12。Python 项目使用 `pyproject.toml` 声明依赖、`uv.lock` 固定解析结果。先安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，再在工具仓库同步环境；后台 worker 使用启动入口的同一个解释器，不修改 sys.path 或 PYTHONPATH。

```bash
# 在 LightRE 仓库运行；仅准备 Python，不调用模型或分析工具。
uv sync --locked
uv run --locked lightre --help

# 静态分析所需系统依赖仍由唯一脚本维护。
bash scripts/install_deps.sh
uv run --locked lightre analyze <绝对目标路径> --workspace <绝对外部workspace>
uv run --locked lightre web <绝对SO分析目录> --host 0.0.0.0 --port 8000
```

从其他目录调用时使用 `uv run --project <LightRE> --locked lightre ...`，调用目录及显式绝对输入路径保持不变。仅浏览已有结果只需 Python 环境，不运行原生依赖安装或静态分析。Codex CLI 仍需另行安装并登录；Ghidra/JDK、Maven、Clang/LLD、Rizin 和 Android sysroot 是外部工具，不作为 Python wheel 的内容。`install_deps.sh` 只准备系统工具，Python 运行依赖统一由 uv 管理，不再依赖系统 Python 的 markdown-it。

`uv sync --locked` 默认以 editable 安装本项目，并包含 dev 组（轻量 API 测试需要 httpx）；部署可用 `uv sync --locked --no-dev`。常规测试为 `uv run --locked python -m unittest discover -s tests -p 'test_*.py'`，不会启动真实模型。需要更新依赖时修改 pyproject 后运行 `uv lock`、检查变更并同步。requirements.txt 和 requirements-dev.txt 是从锁文件导出的 pip 兼容文件，在仓库根目录可用 `python -m pip install -r requirements-dev.txt`；两者不再手工维护，分别用 `uv export --locked --no-dev --no-hashes --output-file requirements.txt` 和 `uv export --locked --no-hashes --output-file requirements-dev.txt` 更新。

安装后提供 `lightre agent/analyze/harness/rehost/web`，也提供 `lightre-agent`、`lightre-analyze`、`lightre-harness`、`lightre-rehost`、`lightre-web` 独立命令。原 scripts/*.py 保留为导入已安装包的薄包装；须先安装项目，不能再靠运行脚本自动添加 src。Python 代码安装后直接使用既有的 agents、workspace、recovery 等模块，不需要手动配置导入路径。

分发构建与独立工具安装：

```bash
uv build
uv tool install /absolute/LightRE/dist/lightre-0.2.0-py3-none-any.whl
lightre --help
```

wheel 包含 Web HTML/静态资源、Ghidra Java 导出脚本、模型 prompts、rehost workflow 和格式化风格；运行不依赖源码仓库。源码 editable 环境默认从仓库 tools 查找外部工具；wheel 环境默认从 `$XDG_DATA_HOME/lightre/tools`（未设置时 `~/.local/share/lightre/tools`）查找，亦支持 `LIGHTRE_TOOLS_DIR`、`GHIDRA_HOME` 和 PATH。为复用已有原生工具可显式设置 `LIGHTRE_TOOLS_DIR=/path/LightRE/tools`；统一系统安装脚本也接受这个变量。Python 安装不会自动下载 Ghidra 或执行原生安装。

分析入口会打印 SO 目录及后台任务状态。已有静态结果和候选会复用。已有相同 SO 的队列运行时直接读取保存结果与进度，不重复执行分析入口；当前入口在 worker 持锁时会拒绝再次导入。`--static-only` 执行静态导出与模块初稿生成，不新建 LLM 任务，也不主动停止已有队列；重复 `--rva 0x23314` 可限定本次还原函数（包括已有候选），用于定向分析，不作为默认流程。`--force` 重新导出静态索引，不是强制重跑全部 LLM。重导出前先暂停该 SO 的活跃队列并等待 worker 退出。未变函数的源码保留；范围变化或不再独立成函数的旧代码及 SQLite（含旧版评价记录，如有）保存到 history/，边界关系保存在 references/ghidra/boundaries.json，关联知识仍可读取。

Codex 非交互命令是 `codex exec`，`-p` 表示配置 profile。可选环境变量：

- `GHIDRA_HOME`：可选 Ghidra 安装目录，默认使用所选 tools 目录下的 ghidra。
- `LIGHTRE_TOOLS_DIR`：外部工具根目录；源码安装默认仓库 tools，wheel 安装默认用户数据目录下 lightre/tools。
- `LIGHTRE_GHIDRA_ANALYSIS_TIMEOUT` / `LIGHTRE_GHIDRA_FUNCTION_TIMEOUT`：参考分析与单函数反编译时限，默认 300 / 60 秒。
- `LIGHTRE_CODEX_WORKERS`：同时运行的批次数，默认 `8`；分析入口可用 `--jobs N` 为新任务覆盖，`1` 为串行。复用已有活动任务不会修改其 workers，429 降低的 effective_workers 会随该任务保存。
- `LIGHTRE_CODEX_BIN`：Codex 可执行文件，默认从 PATH 找 `codex`。
- `LIGHTRE_CODEX_PROFILE`：现有 Codex profile 名称，默认不指定。
- `LIGHTRE_CODEX_SANDBOX`：默认 `danger-full-access`，使用当前容器的执行权限，适用于不能创建 sandbox namespace 的容器；允许 namespace 的环境可设为 `read-only`。提示要求只读调查，候选由 LightRE 统一写入。

调用以参数列表和 stdin 传入，不将评论拼接为 shell 命令。Agent 不使用 MCP，直接读取持久汇编并按需调用本地工具。静态恢复入口不自动执行 unidbg；动态运行使用下文独立的 harness 入口。安装脚本检查所装 Ghidra 的 Java 要求，Ubuntu 24 使用 JDK 25；ARM64 容器缺少本机 decompile/sleigh 时从发行包源码构建。

## 通用 Agent 管理

`src/agents/` 提供跨进程的 `AgentManager`、`AgentSession` 和 `AgentRun`，命令入口为 `scripts/agent.py`。管理层通过 `codex exec` 执行单轮；同 session 的消息按提交顺序执行，不同 session 可以并行。调用客户端退出不会停止已提交的执行。当前基础层可独立使用，后台 worker 在队列空或暂停后退出；计划中的 LightRE 主服务保持常驻，与这些按需工作进程的生命周期分别管理。指导聊天和后台任务将复用该基础层，尚未完成接入。

所有客户端必须使用同一个管理根目录（`--root`，或 `LIGHTRE_AGENT_ROOT`；未指定时为当前目录下 `.lightre-agents`）。这里的 `agents.sqlite` 只维护通用会话、运行与消息队列，不存函数、源码或 Knowledge，也不替代 SO 的 index.sqlite、现有任务目录与业务检查记录。现有静态恢复、harness 修复及 rehost 调用尚未迁移到此管理层，它们的历史任务不会自动出现在这里。

### 命令行调用

在 LightRE 根目录运行；其他目录为 uv run 添加 `--project <LightRE>`。以下路径和 ID 替换为实际值。`create` 只登记会话，不启动模型；`send` 提交并后台启动。成功结果返回 JSON。

```bash
uv run --locked lightre agent --root /path/workspace/agents create --cwd /path/project
uv run --locked lightre agent --root /path/workspace/agents send --session SESSION_ID --message-file /path/task.md
uv run --locked lightre agent --root /path/workspace/agents status --session SESSION_ID
uv run --locked lightre agent --root /path/workspace/agents run --session SESSION_ID --run RUN_ID
uv run --locked lightre agent --root /path/workspace/agents events --session SESSION_ID --run RUN_ID --cursor 0
uv run --locked lightre agent --root /path/workspace/agents wait --session SESSION_ID --run RUN_ID --timeout 60
uv run --locked lightre agent --root /path/workspace/agents result --session SESSION_ID --run RUN_ID
```

`create` 返回稳定的本地 session ID，`send` 返回本次 run ID。`run/events/result/wait` 省略 `--run` 时选当前执行中的 run，没有活动 run 时选最后提交的 run；为了指向确定的一次执行，模块应保存并显式传递 run ID。`list` 列出本 root 的会话。`send --message-file -` 从 stdin 读取，也可用 `--message` 传短消息；`--request-id` 在同 session 内去重，重复键对应不同正文或执行参数时拒绝提交。消息正文不经 shell 拼接。

每次 `send` 的 `--timeout` 默认 600 秒，是执行期限。`wait --timeout` 仅限制客户端等待，超时返回错误但不取消 Agent。`send --schema FILE` 使用可选 JSON Schema，原始最终正文始终保存；管理层检查最终文件非空、请求 JSON 时能解析、退出码、turn.completed 及明确的失败事件，业务正确性仍由调用模块的检查器判定。`wait/result` 对 completed 返回 0，其他终态返回 1；参数、查询或等待错误返回 2。

```bash
uv run --locked lightre agent --root /path/workspace/agents pause --session SESSION_ID
uv run --locked lightre agent --root /path/workspace/agents resume --session SESSION_ID
uv run --locked lightre agent --root /path/workspace/agents cancel --session SESSION_ID --run RUN_ID
uv run --locked lightre agent --root /path/workspace/agents cancel --session SESSION_ID
```

pause 禁止领取下一条消息，允许当前 run 结束；resume 继续尚未执行的消息，不重试已经结束的 run。取消指定 run 时，只影响该轮；取消整个 session 时，同时取消待执行消息并请求停止当前轮。运行中取消需等待终态，不能把请求已接收当作进程已退出。执行失败、超时、运行中取消或中断会暂停后续消息；检查实际文件后显式 resume，或提交新的纠错消息，避免在未知改动上自动重复执行。

### Python 与其他 Agent 调用

Python 使用安装后的同一环境（例如 `uv run --locked python`）直接导入包后使用同一接口。其他 Agent 可通过 shell 调用上述 CLI；它们重新取得的是持久记录的句柄，不需要启动者的 Python 内存。

```python
from agents import AgentManager

manager = AgentManager('/path/workspace/agents')
agent = manager.create('/path/project')
run = agent.send_message('调查当前项目，并说明下一步。', request_id='investigate-1')
session_id, run_id = agent.id, run.id  # 保存到所属功能模块的任务记录

# 另一个 Python 进程使用相同 root 与 ID
agent = AgentManager('/path/workspace/agents').get(session_id)
run = agent.get_run(run_id)
state = run.poll()                    # 非阻塞；status、terminal、时间、退出码和错误
page = run.read_events(cursor=0)      # events、errors、下次使用的字节 cursor
if state['terminal']:
    result = run.result()             # 完整 text；请求 JSON 时另有 parsed

next_run = agent.send_message('补充：优先调查初始化路径。')
```

`manager.get_run(run_id)` 可直接恢复某次运行；`agent.get_run()` 选择活动或最后提交的一轮；`agent.run(message, ...)` 是提交并等待的便捷接口。`agent.snapshot()` 返回 idle、queued、running 或 paused，以及 active_run_id、queued 数量、各 run 状态、worker_alive 和 capabilities。进程意外退出后，仅凭退出或遗留 PID 不认定成功：失去执行租约的 running 标为 interrupted 并暂停。worker 已死但 CLI 尚持租约时不重复启动；已记录身份的孤立 CLI 可通过 cancel 停止。尚未执行且没有 worker 的队列可调用 resume 重新调度。

默认会话保留 Codex 上下文，从 `thread.started` 保存独立的 thread_id，下一条使用精确 `codex exec resume <thread_id>`，不使用 `--last`。`manager.get()` 也接受该管理根目录中已登记的 Codex thread_id；它不能导入任意外部聊天会话。`create --ephemeral` / Python `persistent=False` 表示每轮都是独立 exec，不自动继承上一轮模型上下文，调用模块须显式提供文件和检查记录。

`send_message` 的当前投递方式是 `next_turn`：忙时排队，空闲时启动下一轮；queued 不表示模型已读取或修改已应用。`--delivery immediate` / `delivery='immediate'` 明确返回不支持，不伪装成实时聊天。能力查询为 `live_messages=false`，以后需要时可增加支持即时 steering 的后端。协议依据为 OpenAI Docs：[非交互执行及恢复](https://developers.openai.com/codex/noninteractive/)、[app-server 的 turn/steer](https://developers.openai.com/codex/app-server/)。

每个功能模块持有自己的 session/run ID 和主循环：提交具体任务，轮询是否在执行，结束后核验实际产物，再决定发送纠错消息、创建其他 Agent 或停止。completed 只表示一次 CLI 调用返回完整结果，不能直接标记功能完成。会话内串行不等于共享项目写锁；源码发布、SO/harness 锁、并发额度和检查时的产物一致性由功能模块管理，接入现有流程时必须保留。

快速检查：`uv run --locked python -m unittest discover -s tests -p 'test_agents.py' -v`。测试使用真实临时存储、Python/CLI 客户端、后台 worker 和本地假 CLI，不调用真实模型，不验证业务逆向结论。

## 纯汇编输入

已有导出的 SO 工作区可直接给模型汇编，不依赖 Ghidra 伪代码：

```bash
uv run --project <LightRE> --locked lightre analyze \
  <绝对SO分析目录> --input-mode assembly --rva 0x1c07c --jobs 1
```

此模式 target 是已导出工作区目录，不是原始 SO；不接受静态导出参数 `--workspace/--name/--force/--static-only`。省略 `--rva` 时选择尚无 LLM 候选的函数，已有 LLM 候选默认复用；显式指定 RVA 可重做该函数。该模式跳过 Ghidra 参考读取与模块初稿生成，不启动 Ghidra。函数目录及汇编仍复用已有静态结果，不承诺绕过新二进制导入。

每批任务生成 `batch-0000-0x0001c07c.asm`（含全部已保存指令、原始字节、范围及地址换算说明）和对应 `.knowledge.md` 快照。全部已保存汇编和知识直接内嵌在同一初始提示词，不只提供快照路径，模型无需先分页读文件。内嵌格式保留全部指令和地址，直接分支/调用目标转为 RVA，其他地址操作数保留原体系；原始机器码和操作数保留在 .asm 快照中。仅遇到具体疑点时才读取文件或用 objdump 补核。调用前检查 Codex 单条输入的 1,048,576 字符上限；超限时保存完整提示词、记录明确的执行失败，不截断输入、不调用模型，也不自动重试同一超限请求。模型侧的其他上下文错误仍按实际日志记录。已保存范围可能漏块，快照完整不意味着静态函数边界完整。模型禁止读取 Ghidra 伪代码或调用 Ghidra，允许保留 goto、平坦化和低层表达式，不要求本轮去混淆。

纯汇编模式保留 shell 工具及本机推理配置，禁用 plugins、apps 与子 Agent，每批 600 秒，输出完整 code 或 null 及 notes，不使用首轮局部修改协议。沿用队列、单任务取消、暂停恢复、失败有限重试和统一发布，任务保存 input_mode=assembly；与 reference 请求分别去重，新任务不自动追加深入分析。没有形成候选时仍记为模型未解决，不用占位代码凑覆盖。

内部调用：`start_task(reader, [0x1c07c], workers=1, input_mode='assembly')`。定向请求尊重现有暂停状态；需按用户意图用 `recovery.queue.resume(reader)` 恢复，不能重复提交任务推动。当前 Web 继续按钮使用默认 reference 请求，并自动将既无初稿也无已有候选的函数分流到 assembly 批次；显式强制纯汇编使用上述 CLI 或内部 API；Web 沿用已有队列、进度与源码展示，不需重启。

## 自动运行

批量还原是 LightRE 分析流程中的一步。通过 Skill 启动分析，或由自动化脚本调用同一个分析入口：

```bash
uv run --project <LightRE> --locked lightre analyze <绝对目标路径> --workspace <绝对外部workspace>
```

入口完成 Ghidra 静态导出与模块初稿后，自动在后台执行 Codex 还原，随后退出；已有静态结果会复用，已有 LLM 候选会跳过，Ghidra 初稿仍会处理。关闭聊天、终端或 Web 不会停止后台任务，不需要人工逐个函数发起。进度和结果保存到 workspace，Web 负责查看及反馈。

不提供独立 recovery CLI。分析入口、Web 的继续按钮及评论流程共用内部 `src/recovery/` 能力和任务锁。每个任务处理指定范围一轮；新建任务只执行一轮，模型未解决项保留原因；评论仍可主动续做，旧任务显式启用的自动续做保留。调度器在队列清空后退出，新请求自动启动。评论优先，同优先级 FIFO；健康批次继续运行，评论优先取得下一个可用并发名额，同一 RVA 的任务依次执行。未解决函数保留原因，单批执行失败有限重试后保留失败，继续处理其他函数；存储和来源校验失败才中止任务。详细规则见 [TASK_QUEUE.md](design/TASK_QUEUE.md)。原生 Codex CLI 是工具内部的执行依赖。

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

编辑时保留模块头及成对的 JSON 标记；重命名和移动保留 ID。当前每段仅支持一个 origins RVA，多对多仍待实现。ID 和 RVA 都以所属 SO 分析目录为范围，跨 SO 引用/导出要另附原始 SO 身份；JSON 标记本身不携带 SO 哈希，不能检测手工从其他 SO 目录复制的同名片段。词法扫描跳过字符串和普通注释中的示例，损坏、重复或不匹配的标记会报错。旧标记仍可读取，保存该函数时转换；全部旧模块可在任务空闲时调用 `from recovery import migrate_source_markers; migrate_source_markers(reader)`，逐文件原子转换并核对正文。详细约定见 [源码定位设计](design/SOURCE_NAVIGATION.md)。

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

## Rehost 从零生成

2026-11-02 按用户修正实施。产品提供调查、工程生成、运行与动态补环境的 workflow，
不按目标名称维护预制 Java harness。新入口为 [scripts/rehost.py](../scripts/rehost.py)，
模块为 [src/rehost](../src/rehost)，底层复用 [执行契约](USAGE.md#harness-配置与运行)。
候选源码恢复、双端差分和完整 VM 语义恢复仍属于后续范围。

### 从未知目标开始

```bash
uv run --locked lightre rehost start /workspace/my-so \
  --goal '调查入口及初始化，运行提供的输入，保存实际输出和环境缺口' \
  --input /samples/program.bin --material /samples/resources \
  --max-rounds 5
```

`my-so` 是已有 SO 分析目录，复用其 workspace；也可直接给 Android ARM64 SO，
另加 `--workspace /workspace` 导入。`--input` 和 `--material` 可重复，均不预设文件用途。
目标可无种子启动入口调查；必要输入缺失时保留缺口，不宣称完整路径已运行。
复杂目标说明可以用 `--task-file task.md` 代替 `--goal`。

每次 `start` 创建新的 `workspace/harnesses/<SO名称>-rehost-<id>/`，
其中只有公共 Maven 依赖和 `HARNESS.md`，没有目标 Java 实现。
不复制 DG/minilua、测试 fixture、既有 workspace 或旧 Run 的 harness。
已有目标的旧工程、旧 Run 保留；旧关联保存到本次 `rehost/<id>/previous-association.json`，
当前 `harness.json` 指向新工程。同一 SO 的两个历史任务不能同时成为当前关联，
恢复较早任务前需明确恢复它原有的关联，不能对错工程续跑。

### Workflow 与代码分工

| 资源 | 职责 |
| --- | --- |
| [workflow.py](../src/rehost/workflow.py) | 新建空目标工程、固定输入/材料、保存 readelf 检查、关联通用执行器 |
| [WORKFLOW.md](../src/rehost/workflows/unidbg/WORKFLOW.md) | Codex 本轮入口与交接规则，按实际缺口选择下面的操作 |
| [DISCOVER.md](../src/rehost/workflows/unidbg/DISCOVER.md) | 调查 ELF、入口、ABI、初始化、资源与输入传递 |
| [BUILD_AND_RUN.md](../src/rehost/workflows/unidbg/BUILD_AND_RUN.md) | 从零编写 Java、构建并实际调用原生入口、提取输出 |
| [ENVIRONMENT.md](../src/rehost/workflows/unidbg/ENVIRONMENT.md) | 根据真实 JNI、文件、系统调用及设备依赖缺口迭代补环境 |
| [pom.xml](../src/rehost/workflows/unidbg/pom.xml) | 公共 unidbg/Java 技术依赖，不含目标 ABI、RVA 或环境实现 |

Python 先保存目标身份、输入契约及静态检查，再调用独立 `codex exec --ephemeral`。
Agent 读 workflow 和当前报告，在授权工程内从零实现 `lightre.RehostHarness`，
可在一次 exec 内反复调查、构建和运行。外层等待写入进程结束后从源码快照重新构建并运行种子，
将实际失败及上轮完整正文交给下一轮。空工程不是不可恢复错误，会进入首轮生成。
执行状态、锁、每轮结果、取消与恢复复用 [Goal Loop](design/GOAL_LOOP.md#最小落地先完整保存返回正文)。

JNI 是否存在、类/签名、系统依赖、实际 ABI 与入口都在运行时调查，不按文件名分流。
缺少 JNI 时记录完整签名和调用位置，补足一个具体行为后重放；不能统一返回零/null，
不能跳过初始化、修改 ready 标志或绕过原生逻辑假装环境已补齐。
必要环境替身注明来源、假设与范围；缺少真实设备资料时保留限制。

`HARNESS.md` 记录当前调查、适配计划、实现、环境假设和缺口，随代码保存到 Run；
外层执行器维护最近检查区，历史副本不补写。所需小型运行资源及辅助代码放在 `src/` 内，
以便随 Maven 工程进入快照；大型外部依赖仍须保留并明确引用。

### 运行观察与固定预期

未知 SO 通常没有已知输出。默认 `observe` 模式固定种子、真实构建并执行，
要求 observation 包含非空 `entry`、整数 `native_calls >= 1`、`status: "returned"` 与 `missing_environment: []`；
缺失输出、未调用入口或仍有环境缺口不能取得 observed。
所有种子取得运行观察后停止为 `status=observed`、`passed=false`，保留验证缺口。
这说明执行器运行了生成的 harness 并采集了它报告的原生观察，不独立证明目标业务正确、
所有内部路径均已执行或所有设备替身真实。观察提取代码与原始日志仍可审阅。

已有独立已知输出时使用 `--cases /path/cases.json`，必要时加入独立 `--checker` 和 `--artifact`。`--cases` 与种子 `--input` 互斥；`--checker` 必须与 `--cases` 一起提供。
输入和检查器格式见 [执行契约](USAGE.md#harness-配置与运行)。固定用例、原始 SO、预期及检查器
在模型可编辑工程之外；模型不能发明预期、改变比较规则或修改基准来取得完成。
全部固定检查通过才可标记该范围 `complete`。

两种模式都保存原始日志、输入、源码、说明及内容依据。observe 重放还核对原始 observation
哈希并比较记录内容；重放一致证明这次记录可复现，不把它升级成独立行为预期。

### 续做、运行与重放

```bash
uv run --locked lightre rehost resume /workspace/my-so \
  --task /workspace/my-so/tasks/harness-<id> --max-rounds 5

uv run --locked lightre harness check /workspace/my-so
uv run --locked lightre harness run /workspace/my-so --case seed-1
uv run --locked lightre harness replay /workspace/my-so /workspace/my-so/runs/<id>
uv run --locked lightre harness cancel /workspace/my-so/tasks/harness-<id>
```

`resume` 才保留同一任务的生成工程和失败经验，不创建新的初稿。
`--max-rounds` 是调用方可选上限；未设置时不设固定轮次上限。
每个子进程仍有可调超时；连续 CLI 故障、固定检查器/资源错误会保留非完成状态并停止，
前台中断或 cancel 会停止在途进程。上限或取消不作为完成依据。
CLI 返回 0 可以表示 observed，最终是否通过应读取状态和 `passed`，不能只看退出码。

系统依赖仍由 `scripts/install_deps.sh --runtime-only` 安装；没有新增安装平台或运行后端。
产品不要求用户预先提供 Java harness，也不要求先开发特定目标模板。

### 本次验证范围

新增临时目录与真实子进程替身检查：空 Java 工程进入生成、首轮缺环境触发后续修复、
完整返回交接、全新 start 与同任务 resume、固定预期通过、无预期保持 observed、种子保护与观察重放。
这些检查验证编排与持久化，不代替真实模型生成或 unidbg 集成验证。
本次未调用真实模型从零生成 DG/minilua harness；原模板时期的运行记录只作为
[历史目标证据](milestones/v0.31/DELIVERY.md)，不能记为新 workflow 已通过真实目标验证。


## Harness 配置与运行



`src/execution/` 不识别目标名称，不包含样本 ABI、JNI 注册、VM 地址、业务预期或产物名。当前运行后端是 Android ARM64 SO + Maven/Java/unidbg；“通用”指该后端内可接入新目标，不表示支持其他架构或构建系统。

核心负责构建、执行、进程超时与取消、日志、锁、快照、结果比较和重放。目标适配由外部 Java harness 承担；专用参数和检查规则由显式配置的独立 Python 检查器承担。仓库 `examples/` 保存示例，没有工具内置目标白名单。

### 关联任意目标

先生成说明，再按调查结果创建或适配工程：

```bash
uv run --locked lightre harness guide /workspace/harnesses/my-target \
  --target my-target --instructions /workspace/my-target-adaptation.md

uv run --locked lightre harness configure /workspace/my-so \
  --harness /workspace/harnesses/my-target --main-class company.MyHarness \
  --cases /workspace/my-cases.json --checker /workspace/checkers/my-target.py \
  --artifact result.dat --artifact traces/events.json
```

`--instructions`、`--checker`、`--artifact` 都可省略。传新 SO 文件时另加 `--workspace`，导入原 workspace。`configure` 关联现有工程，生成缺失说明，不生成目标 Java 实现，不覆盖不同的已有配置。

生成的 `harness.json` 示意：

```json
{
  "path": "/workspace/harnesses/my-target",
  "parameters": {"main_class": "company.MyHarness"},
  "cases": "/workspace/my-cases.json",
  "checker": "/workspace/checkers/my-target.py",
  "artifacts": ["result.dat", "traces/events.json"]
}
```

路径相对于 `harness.json`，也可使用绝对路径。`main_class` 必须明确指定；`artifacts` 中所有文件都必须生成，保存 SHA256，重放要求字节一致。路径必须位于 Run 内，不能覆盖工具输入、检查器或记录，也不能通过符号链接指向外部。用例和检查器必须位于可编辑 harness 之外，修复模型不能修改这些基准。可用 `protected_files` 数组额外声明修复任务需固定的文件。

Java 调用协议统一为：

```text
java -cp <classes:dependencies> <main_class> SO request.json observation.json
```

入口处理自己的 ABI、JNI、初始化和参数，向第三个路径写 JSON observation；相对请求路径的输入文件由 harness 读取。产物写入 observation 所在 Run 目录，文件名由本目标约定。

### 用例与默认检查

用例 JSON 是非空数组，`id` 唯一：

```json
[
  {
    "id": "seed",
    "request": {"input_file": "inputs/seed.bin", "mode": 3},
    "input_sha256": "<输入文件 SHA256>",
    "expected": {"status": 0, "result": "known output"}
  }
]
```

用例中的相对输入路径按用例 JSON 所在目录解析。`run --request` 的相对输入路径按请求 JSON 所在目录解析。`input_hex` 也可提供字节；无二进制输入时可只传 JSON 参数，输入 SHA256 使用空字节的哈希。

没有专用检查器时，核心只规范化可选输入文件，其他参数作为不透明 JSON 传给 Java。输入哈希和参数（包括嵌套值类型）必须精确匹配已声明用例；`expected` 必须是非空对象。结果逐字段比较值和类型，未列入预期的观测字段可保留。

### 专用检查器

检查器是可信、固定的单个 Python 文件，只依赖标准库，不依赖同目录模块或 LightRE 包；它作为代码运行，不是沙箱内的配置。需要专用参数校验、计算型预期、JNI/VM/文件结构校验时实现：

```python
def normalize(request, base=None):
    # 返回规范化请求 dict、实际输入 bytes、来源 dict 或 None。
    # 保存输入的统一请求路径为 input.bin；路径按 base 解析。
    return normalized, data, origin


def expected(normalized, data, rows):
    # 从固定用例取得预期，或按已知独立基准计算；不能信任 harness 自报通过。
    return nonempty_expected_dict


def validate(observation, run_root):
    # 追加目标专用差异。通过时返回空列表。
    return list_of_difference_strings
```

外层仍比较预期并核对所有声明产物、SO、源码、输入、检查器和依赖完整性。检查器异常记录为失败。检查器的输出不能代替真实 harness 执行。

每次 Run 保存 `contract.json`（用例、入口、产物声明）、`checker.py`（若配置）、`expected.json`、请求与输入、Java 工程和说明；manifest 保存其哈希。重放使用历史检查器与用例快照，不读取当前版本的检查器或原输入。SO 和 Maven 依赖仍须保留在记录的路径。

旧实现保存的 Run 缺少检查器快照，不能证明历史检查规则。它们保留只读；新执行器明确拒绝直接重放，不按目标名称推测检查器。将现有工程配置为显式契约后重新运行，取得完整的新 Run。旧修复任务绑定旧检查器/执行代码的哈希；核心变更后应使用现有工程和新契约建立新任务，历史任务保留。没有缺失契约的历史 Run 即使缺少 HARNESS.md，仍可重放且不会补写历史说明。


### 运行、检查与记录

```bash
# 在 LightRE 根目录运行，其他目录请使用脚本绝对路径。
uv run --locked lightre harness cases <SO目录>
uv run --locked lightre harness run <SO目录> --case seed
uv run --locked lightre harness run <SO目录> --request /absolute/request.json
uv run --locked lightre harness check <SO目录>
uv run --locked lightre harness replay <SO目录> <旧Run目录>
```

`--case` 与 `--request` 互斥；不指定时运行首个配置用例。输入 JSON 中的参数由目标决定，没有全局默认 ABI。每次启动独立 Java 进程与模拟器。`--timeout` 默认 300 秒，分别限制 Maven、Java 或每轮 Codex 进程，不是任务总时限。只运行此路径不需要 Ghidra，也不会创建静态候选任务或打开 Web。

环境需要 Python 3.10+、JDK 17+ 和 Maven；开发 smoke 还需要 Clang/LLD，统一安装入口为 `bash scripts/install_deps.sh --runtime-only`。示例使用 unidbg 0.9.9 / Unicorn2 / Android ARM64 API23，依赖版本固定在各自 pom；首次 Maven 构建需要联网，随后复用缓存。

```text
<SO目录>/runs/<run_id>/
  manifest.json          # SO、源码、请求、输入、契约、检查器、依赖及产物哈希
  contract.json          # 固定用例、Java 入口和产物声明
  checker.py             # 配置了独立检查器时保存
  harness/               # 执行前 HARNESS.md、pom 与 Java 源码快照
  request.json
  input.bin
  expected.json
  observation.json       # 实际观察，失败时可能不存在
  verification.json      # 每次尝试的外部报告，失败也保存
  versions/java/
  versions/mvn/
  build/
  execute/               # 命令、退出码、stdout/stderr
  <声明的产物>
```

加载 SO、Maven 编译成功、Java 正常退出和模型声明完成均不等于通过。固定预期模式必须核对预期、专用规则、产物与完整性，CLI 仅在通过时返回零；失败原因从 verification.json 和对应日志读取。

### 修复、取消与恢复

```bash
uv run --locked lightre harness repair <SO目录> --max-rounds 3
uv run --locked lightre harness cancel <SO目录>/tasks/harness-<id>
uv run --locked lightre harness repair <SO目录> \
  --task <SO目录>/tasks/harness-<id> --max-rounds 3
```

先按目标配置的全部固定用例检查，已满足则记录依据并停止；否则独立调用 `codex exec --ephemeral` 修复可编辑工程。每轮先读 HARNESS.md，更新适配计划再改代码。固定 SO、用例、预期、检查器与任务历史不由模型修改，未通过时自动反馈原始记录继续。没有目标类型白名单；接入通用循环不代表任意目标已经能自动修好。

任务保存 task.md、contract.json、protected.json、输入及检查器快照、state.json；每轮保存 prompt.md、完整 result.md、events.jsonl、stderr.log、execution.json 和 verification.json。默认每次调用最多三轮，达到上限为 limit，连续两次 CLI 故障或空回复停止并记录原因；取消后可恢复同一任务，恢复时重新检查当前工程。Ctrl-C/SIGTERM 也会请求取消，并终止在途进程组。不能在旧写入者仍运行时启动新任务；共享 SO 与 harness 都有文件锁，harness 任务不进入静态候选队列。

受控 Linux 容器默认使用 danger-full-access。可通过 LIGHTRE_CODEX_BIN 指定模型 CLI、LIGHTRE_CODEX_PROFILE 指定已有 profile；显式 sandbox 覆盖优先取 LIGHTRE_HARNESS_SANDBOX，其次取 LIGHTRE_CODEX_SANDBOX。源码与说明、输入、检查器和依赖被修改后不能沿用旧通过结论。

仅观察、bootstrap/workflow 已由 scripts/rehost.py 接入；本次新增编排通过临时文件/外部进程替身检查，真实模型从零生成与真实 unidbg 集成尚未运行。observe 的 CLI 成功退出只表示取得观察，报告仍为 passed=false；重放核对原 observation 的哈希与内容，不把历史观察当作独立预期。

## 样本入口

样本的 ABI、JNI、环境 fixture 和构建细节随各自项目维护，通用执行器不包含这些规则：

| 样本 | 专用说明 | 入口 |
| --- | --- | --- |
| DG | [历史适配记录](../examples/dg/ADAPTATION.md) | 使用匹配版本 .bin 经 rehost 从零调查；不再复制目标 Java 模板 |
| Android minilua | [源码构建](../samples/minilua/ANDROID.md)、[Java 适配](../examples/minilua/ADAPTATION.md) | 在原 samples/minilua 构建 SO，再导入原 workspace |
| 开发 smoke | [固定 ABI 与预期](../examples/native-smoke/ADAPTATION.md) | prepare.py 只构建/导入固定测试 SO，harness 由 rehost 从零生成 |

DG 和 minilua 使用不同的 harness；small/large minilua 可共享工程，各 SO 保持自己的契约与 Run。minilua 的 Linux glibc 样本不能当作 Android SO 直接加载。真实运行证据集中在 [V0.31 交付记录](milestones/v0.31/DELIVERY.md)；DG 巨函数没有恢复 C 的已知缺口见 [V0.2 交付记录](milestones/v0.2/DELIVERY.md)。
