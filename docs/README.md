# 文档入口

当前功能和命令只维护一份：[功能说明与 API](USAGE.md)。V0.2 已于 2026-11-01 通过用户人工验收；V0.31 已有通用 harness、DG/minilua 单边运行及最小修复循环的开发结果，仍待人工验收。候选构建、双边差分与完整工程恢复尚未交付。

产品入口方向已明确为常驻 Python 服务、一个指导聊天窗口及多个 Codex CLI 工作会话，服务整合仍待实施；职责及进程关系见[总体架构](design/ARCHITECTURE.md#常驻服务与指导会话待实现)。

## 阅读入口

docs 根目录只保留导航、当前功能、产品需求和路线图。技术设计集中在 design，历史验收与证据在 milestones，背景教程在 reference。

| 内容 | 入口 |
| --- | --- |
| 当前功能、操作、命令与 API（含 rehost） | [USAGE.md](USAGE.md) |
| 产品需求与人工验收口径 | [REQUIREMENTS.md](REQUIREMENTS.md) |
| 后续开发顺序与完成条件 | [ROADMAP.md](ROADMAP.md) |
| 架构、Goal Loop、执行差分、源码导航、任务队列及 VM 方法 | [技术设计目录](design/README.md) |

## 版本记录

milestones 只保存版本范围、人工验收条件和有依据的交付记录，不再保存 USAGE。历史结果描述当时的实现，不覆盖当前功能说明。

| 版本 | 文档与状态 |
| --- | --- |
| V0.1 | [历史交付](milestones/v0.1/DELIVERY.md)，早期 Rizin 流程已被后续默认入口替代 |
| V0.2 | [已验收范围](milestones/v0.2/PLAN.md)、[交付证据与已知缺口](milestones/v0.2/DELIVERY.md) |
| V0.3 第三阶段 | [总体计划：unidbg 运行、候选构建、双边差分与反馈修复](milestones/v0.3/PLAN.md)，按小步交付，尚未整体完成 |
| V0.31 | [当前验收范围](milestones/v0.31/PLAN.md)、[开发实测与限制](milestones/v0.31/DELIVERY.md)，待人工验收 |

V0.3 PLAN 维护第三阶段的范围、开发拆分与整体验收，V0.31 PLAN 维护其中首个增量的具体条件；ROADMAP 只维护长期能力顺序。D1–D4 是工具能力顺序，M0–M6 是单个 VM 的分析进展，均不等于新的产品版本已经启动或通过验收。

## 样本、参考与运行指令

目标 ABI、环境和构建说明随各自工程维护：[DG](../examples/dg/ADAPTATION.md)、[minilua Java](../examples/minilua/ADAPTATION.md)、[minilua Android 源码构建](../samples/minilua/ANDROID.md)、[开发 smoke](../examples/native-smoke/ADAPTATION.md)。实跑证据归入对应 DELIVERY，不在样本说明复制一套版本状态。

[ARM64 逆向教程](reference/ARM64_RE_R%26D.md) 仅作背景参考，其中 Rizin、Frida、LLDB 的例子不代表 LightRE 当前入口。模型执行指令在 [恢复 prompts](../src/recovery/prompts)、[harness prompts](../src/execution/prompts) 与 [rehost workflows](../src/rehost/workflows/unidbg)，不在 docs 复制运行时 prompt。

更新文档时，直接修正所属文档的当前正文，不用追加相互冲突的“后续补充”。新增技术设计放在 design，不在根目录散放或新建版本 USAGE。历史故障与实测保留在 DELIVERY，规划明确标记尚未实现；不因代码存在或开发检查通过修改人工验收状态。删除或移动文档时同步修复仓库中的路径及锚点引用。
