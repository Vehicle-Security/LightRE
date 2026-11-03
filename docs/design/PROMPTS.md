# Prompt 与 Markdown 资源

Codex CLI 读取的 Prompt、Skill 和 Workflow 指令正文全部由使用它的业务模块维护。各模块的 `prompts/` 保存模板，`skills/`、`workflows/` 保存需要相互引用的操作文档和配套资源，发布后保留原目录结构。

Skill 的使用范围是 LightRE 自己启动的 Codex CLI：由所属模块随包提供，执行时在任务 Prompt 中注入名称、适用描述和文档路径，供 Codex 按需读取。Recovery 使用一个内部 Skill，为每批保存文档快照和 SHA256 清单；无需面向外部提供安装入口或全局注册。两个任务模板均注入该 Skill，按需只读取证。

| 模块 | 资源 | 职责 |
| --- | --- | --- |
| `lightre` | `prompts/EXPLORE.md` | 选择目标 SO |
| `recovery` | `prompts/RECOVER.md`、`FEEDBACK.md` | 首轮/指定函数代码改进，以及根据用户意见修改；汇编作为输入条件分支 |
| `recovery` | `skills/lightre-recovery/SKILL.md`、`references/ASSEMBLY.md` | 一个内部方法入口及一份机器取证参考 |
| `rehost` | `prompts/`、`workflows/unidbg/` | Harness 说明、修复要求、轮次上下文及 unidbg 操作依据 |
| `analyzers` | `prompts/BOUNDARY_CHANGES.md` | 给已有知识补充边界变更和旧资料读取说明 |

主流程只调用 Rehost 模块的自动启动/续跑入口。默认调查目标由 `rehost/workflows/unidbg/TASK.md` 定义；调用方可以显式传入任务描述，主流程不另存一份 Rehost 指令。

Recovery 的全部模板、调用入口、触发条件、内部 Skill 与实际输入留档，见 [Recovery Prompt 映射](RECOVERY_PROMPTS.md)。

尚未接入执行器的工程恢复与差分模板保存在 `docs/future/prompts/`，不作为当前运行时资源发布。当前模块中的模板分别对应实际调用或生成文档的用途。

Python 准备任务、路径、JSON、状态和输出 schema；操作要求、默认任务、补充指令、生成文档中的说明都保存在 Markdown 中，不在代码里拼接指令字符串。异常消息、运行诊断及机器协议标记属于程序数据。

共享的 `lightre.prompt.render_prompt()` 使用 Jinja2 读取 UTF-8 模板。`{{NAME}}` 插入数据，`{% if ... %}` 维护条件与默认说明，`{% for ... %}` 维护列表。顶层变量名使用大写；模板使用严格未定义检查，缺少变量或语法错误会连同文件路径报错。关闭 HTML 转义并保留末尾换行，便于原样传递 C、汇编、Markdown 和 JSON；`json` filter 使用保留 Unicode 的 JSON 编码。

模板是随代码审核的可信程序资源。用户评论、源码和路径只作为数据传入，不会再次解释其中的 Jinja 表达式。不要对任务数据进行二次模板渲染。

模板随程序版本发布。模块通过自身文件位置读取资源，既适用于源码运行，也适用于 wheel 安装；不依赖当前工作目录和源码仓库。每次实际发送的完整 Prompt 仍保存到现有任务或 Agent run 目录，便于追踪当时的输入。

Setuptools 在构建时递归收集所有模块下的 Markdown，并按原相对路径复制到发布包。源码包通过 `MANIFEST.in` 包含这些文件，wheel 通过通用 `package-data` 规则包含它们；新增模块或更深层的 Markdown 不需要逐文件登记。工程所需的 Java、XML、网页等资源继续按各自类型配置。

模板和 Skill 从模块安装位置定位，不依赖当前工作目录或源码仓库；Skill 复制到任务快照后保留 references 的相对目录，任务 Prompt 注入快照入口的绝对路径。

发布检查逐文件比较源码、wheel 和源码包中的 Markdown 内容，并检查 wheel 是否残留已经删除的模板。另在全新虚拟环境实际安装 wheel，使用隔离 Python 从源码目录之外运行资源、模板和 Recovery 批次测试，确认模块及文档来自该安装位置。构建与检查命令见 [使用指南](../USAGE.md#prompt-资源发布)。
