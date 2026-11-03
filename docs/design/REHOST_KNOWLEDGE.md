# Rehost 知识库

## 定位

Rehost 的经验分三层，各层职责与生命周期不同：

| 层 | 位置 | 生命周期 | 维护者 |
| --- | --- | --- | --- |
| 单工程操作说明 | harness 工程 `HARNESS.md` | 随该 SO 的工程存续 | agent 每轮更新，外部检查区由工具维护 |
| **workspace 知识库** | `<workspace>/rehost-knowledge/*.md` | 跨 SO、跨任务持久 | agent 追加条目，工具播种模板 |
| 轮次快照 | 任务 `round-*/` | 只读历史 | 工具自动保存 |

此前只有第一层：每个 SO 的 harness 各自记录经验，换一个 SO 就从零踩坑。
知识库补第二层：unidbg 补环境配方、SO 通用模式、失败→解法记录在同一 workspace 的
多个 SO 分析间共享。第三层快照保证结论可追溯，不因条目被修改而丢失证据。

## 实现

- 模块：`src/rehost/knowledge.py`；种子模板：`src/rehost/knowledge/*.md`
  （`INDEX.md` 使用说明与条目格式、`UNIDBG.md`、`SO_PATTERNS.md`）。
- 播种：`repair()` 进入写锁后调用 `knowledge_directory()`，缺省文件从模板创建，
  已有条目永不覆盖。
- 注入：每轮 prompt（`ROUND.md` 的 `{{KNOWLEDGE}}`）只携带根路径与文件清单，
  agent 按需读取，不整库载入。
- 写入：agent 结束前追加有真实运行依据的条目；`WORKFLOW.md` 放行该目录，
  其余非 harness 路径保持只读。条目格式（症状/原因/解法/来源）由 `INDEX.md` 约定。

## 边界

- 知识库不是任务状态；每轮结论以 `HARNESS.md`、`verification.json` 和 Run 记录为准。
- 不做结构化校验（区别于 recovery 的 `knowledge_edits` 协议）：rehost 条目是自由
  Markdown，靠格式约定与"只追加"约束维持质量；出错时条目可被更正条目覆盖说明。
- 每个工程（uv workspace）一个知识库；跨部署共享属未来功能。
