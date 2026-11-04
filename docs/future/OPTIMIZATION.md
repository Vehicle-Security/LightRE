# 简化与优化清单

状态：2026-11-03 全面审计的结论，留给后续版本处理。按 AGENTS.md 原则
（保持简单、可读、可维护），只列有实际收益的动作，不做当前不需要的抽象。
逐项完成后在此打勾并注明版本。

审计快照：src 7377 行（6 包）/ tests 5203 行（~180 用例）/ docs 3662 行（28 文件）。

---

## 一、代码简化

### 1.1 拆分 runner.py 的巨型函数（优先级：高）

`_run()` 149 行，串了 preflight、工具版本检查、Maven 构建、Java 执行、
observation 解析、完整性校验（SO/输入/依赖哈希）、报告组装七件事。
拆为 `_preflight` / `_build` / `_execute` / `_integrity` 四步，`_run` 只做编排。
收益：可读、可单测（现在只能端到端测）。

### 1.2 删除 protected.json 双格式兼容层（优先级：中，有前置条件）

`_protected_sections()` 仍带旧扁平格式分支，`_accept_executor_update()` 带
"输入区归位"补丁（2026-11-03 两次恢复故障的产物）。前置条件：dg-workspace
的在途任务收官、旧任务归档后，直接只支持 `{inputs, executor}` 分区格式，
删两个兼容分支。

### 1.3 删除 guide.py 兼容 shim（优先级：低）

12 行 shim 仅为 tests/ 的旧导入名保留。改 3 个测试文件
（test_execution / test_packaging / test_prompts）的 import 后可删。

### 1.4 JSON 工具归位（优先级：低）

`rehost/storage.py` 的 `read_json/write_json/digest/identifier` 已被
`lightre/main_workflow.py` 反向导入。可提到 `lightre/records.py`，
rehost 从那里导入，消除反向依赖。顺手统一 `lightre` 包内 7 处内联
`json.loads(read_text())` 模式。

### 1.5 contract.py 结构整理（优先级：低）

256 行里 Contract 类与 `baseline_files/case_path/cases` 等模块级函数混杂。
若 V0.4 差分要扩展契约，先把模块级函数收进类，避免两套入口继续生长。

### 1.6 不做的（明确记录，避免反复）

- workflow.py 刚完成两层合并（loop 并入 workflow），不再拆 start()——
  它的 90 行参数校验是入口契约，拆了要多传一次上下文。
- 不引入插件注册、任务服务或通用策略引擎（storage.py docstring 已声明）。

### 1.7 Run 快照瘦身与目录合并（优先级：中，2026-11-03 补充）

实测单个 Run 快照 428K，DG 任务几十个 run 会持续膨胀；`rehost/<id>/`（start
准备材料）与 `tasks/harness-<id>/`（任务控制面）是同一次逻辑任务的两个目录，
task.md 两处出现。

- 快照只保留证据必需项：pom/HARNESS.md/src/ + build/execute 日志 + manifest；
  `target/`（编译产物，重放会重建）与 `scratch/` 不进快照
- 评估 `rehost/<id>/inspection` 并入 `tasks/harness-*/`（或反向），一个任务一个目录
- 更激进方案（content-addressed 硬链接去重）暂不做，先做上面的减法

---

## 二、文档精简

### 2.1 已于本次审计修正

- `design/GOAL_LOOP.md`：示例代码引用已删除的 `rehost/loop.py` 与 `repair()` →
  改为 `workflow.py` 的 `run_rounds`。
- `design/REHOST_KNOWLEDGE.md`：`repair()` → `run_rounds()`。

### 2.2 待做

| 文档 | 问题 | 动作 |
| --- | --- | --- |
| `milestones/v0.3/COMPLETION_PLAN.md`（289 行） | "进行中计划"引用已删除符号（loop.py/repair），内容与 PLAN.md 重叠 | **v0.3 验收时删除或归档**，历史结论并入 DELIVERY |
| `design/REHOST_DESIGN.md` 重构记录节 | 会随重构持续增长 | 只保留最近两次重构记录，旧的删除 |
| `docs/README.md` 与各索引 | V0.3/V0.3.5 收官后需统一刷新版本状态 | 收官时一次处理 |
| `reference/ARM64_RE_R&D.md`（481 行）、`REQUIREMENTS.md`（400 行） | 大但有用（参考资料/需求口径） | 保留，不动 |

原则重申：milestones/ 是历史证据，正文过时**不追改**；design/ 是活文档，
引用必须与代码一致（本次已对齐）。

---

## 三、测试精简

总量健康（5203 行对 7377 行实现），不建议删用例。可做的是**去重复基础设施**：

### 3.1 提取 workspace fixture（优先级：中）

`test_source_api.py`（49 用例）与 `test_candidates.py`（45 用例）几乎每个
用例各自建临时目录 + index.sqlite + 元数据， setUp 模式重复估计 300+ 行。
提取公共 `make_workspace()` helper 到 `tests/util.py`。

### 3.2 改测试导入名（配合 1.3）

三处 `from rehost.guide import ...` → `from rehost.knowledge import ...`。

### 3.3 不做的

- 不追求覆盖率数字、不为重构后的旧名字保留兼容测试。
- 端到端测试（test_execution 的真实子进程流）保持，它是防止
  "验证器自己骗自己"的唯一手段。

---

## 四、时机与顺序建议

1. **现在可做**：文档类（2.2 中除 COMPLETION_PLAN 外的零星项）。
2. **v0.3 验收时**：删 COMPLETION_PLAN、刷新索引。
3. **v0.3.5 实战验证后**：1.2（兼容层）、1.3+3.2（shim 与导入）一起做。
4. **V0.4 开工前**：1.1（拆 _run）、3.1（测试 fixture）、1.4/1.5 顺手。

改模块代码前确认无在途任务对模块做基线校验（运行中校验现只查任务输入区，
恢复时自动重置执行器基线——2026-11-03 修复，但避免在任务运行中重启服务）。
