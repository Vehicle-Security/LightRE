# Rehost 模块设计

目标：对未知 Android ARM64 SO，从零生成可真实运行的 unidbg harness，
以"真实构建+运行+外部检查"的反馈循环逐轮补环境。

## 入口

| 入口 | 路径 | 用途 |
| --- | --- | --- |
| `lightre` 服务 | `lightre/server.py` → `main_workflow.start_workflow` | 完整流程：静态分析→Recovery→自动接续 Rehost |
| `lightre-rehost` 命令 | `lightre/rehost_cli.py` | 单独运行/恢复/推进 Rehost（`--description` 新目标、`--rounds`、`--timeout`） |
| 编程接口 | `rehost.workflow` 的 `start` / `resume` / `continue_analysis` / `run_rounds` | 上述两个入口的底层实现 |

所有入口最终都进 `workflow.run_rounds()`——它是唯一的执行 Owner。

## 分层与文件（5 个文件，自顶向下）

```
workflow.py    编排层：入口（start/resume/continue_analysis）、任务生命周期
               （创建/恢复/受保护基线）、验证与 agent 轮次循环（run_rounds/_round_loop）
runner.py      执行层：Maven 构建、Java 运行、Run 快照保存、结果比对、SO 身份校验
contract.py    契约层：固定用例/预期/独立 checker 的定义、校验与快照
knowledge.py   知识文件层：单工程 HARNESS.md（ensure_guide/record_check）
               + workspace 级知识库 rehost-knowledge/（directory/summary）
storage.py     基础设施：JSON 读写、harness 关联(association)、写锁、digest，
               以及带日志/超时/取消/进程组清理的子进程执行(execute)
```

依赖方向单向：`workflow → runner → contract/storage/knowledge`。
编排只有一层：workflow.py 既是对外入口也是循环引擎，没有中间转发层。

## 数据与目录

| 路径 | 内容 |
| --- | --- |
| `<workspace>/<so分析目录>/tasks/harness-*/` | 任务状态、轮次 prompt/记录、验证报告 |
| `<workspace>/harnesses/<so>-rehost-*/` | harness 工程（pom.xml、HARNESS.md、src/）——唯一可编辑 Java 代码 |
| `<workspace>/runs/<id>/` | 每次真实运行的独立快照与日志 |
| `<workspace>/rehost-knowledge/` | 跨 SO 知识库（详见 [REHOST_KNOWLEDGE.md](REHOST_KNOWLEDGE.md)） |

## 关键约束

- 单写者：`.lightre.lock` + `tasks/.lock` 保证同一时刻只有一个进程推进任务。
- 不可变基线：任务创建时对"任务输入"（task.md、契约、SO、用例）与"执行器代码"
  （rehost 模块）分别做 SHA256 快照。运行中发现任何变化都拒绝；
  显式恢复任务时接受执行器升级（`_accept_executor_update`），任务输入仍严格校验。
- observe/expected 双模式：无固定预期时只报告真实观察（observed，不算 passed）；
  有契约时严格比对预期。
- agent 只能改 harness 工程和知识库条目；状态、契约、历史 Run 只读。

## 重构记录

- 2026-11-03：`guide.py` 并入 `knowledge.py`；`association` 从 runner 移到 storage；
  `_target` 更名 `target_info`；晦涩函数更名
  （`repair/_drive/_verify/_create/_protected` →
  `run_rounds/_round_loop/_check_harness/_new_task/_check_immutable`）。
- 2026-11-03：两层编排合并——`loop.py` 并入 `workflow.py`（入口即循环引擎）；
  `process.py` 并入 `storage.py`。7 文件 → 5 文件。
- 2026-11-03：删除 `workflows/unidbg/PROJECT.md`——"从零实现"与调用协议原本在
  TASK.md、WORKFLOW.md、PROJECT.md 三处重复；协议与 observation 格式统一由 TASK.md
  承载，HARNESS.md 只留通用约定。
