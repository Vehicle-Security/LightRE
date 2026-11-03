# 未来功能设计

本目录存放尚未实现或仍在规划中的功能设计。**版本排期与能力栈统一在
[路线图](../ROADMAP.md#整体规划2026-11-03)维护**，本文件只是设计文档索引。

## 文档列表

| 文档 | 内容 | 版本 |
| --- | --- | --- |
| [DEOBFUSCATION.md](DEOBFUSCATION.md) | 逆向主线工具链：分支触发（fuzz/强制执行）、P-code 切片、SMT/MBA 管线、平坦化结构恢复 | V0.4-V0.6 |
| [DIFFERENTIAL_EXECUTION.md](DIFFERENTIAL_EXECUTION.md) | ARM64 unidbg 双边差分：状态映射、候选构建 Run、比较契约 | V0.4 |
| [SOURCE_MANAGEMENT.md](SOURCE_MANAGEMENT.md) | 源码管理与符号迁移（代码已删，规划恢复） | V0.4 |
| [MAIN_WORKFLOW.md](MAIN_WORKFLOW.md) | Recovery、Rehost、差分的顶层循环 | V0.4+ |
| [VM_RECOVERY.md](VM_RECOVERY.md) | VM 分析方法与语义恢复里程碑（M0-M6，含平坦化/MBA 处理方法） | 方法论，长期 |

尚未接入执行器的[工程恢复模板](prompts/RECOVER_AND_REFINE.md)和
[构建与验证模板](prompts/BUILD_AND_VERIFY.md)保存在 `prompts/`，仅供设计参考，
不参与运行时打包。

功能实现后设计文档移入 [design/](../design/)，实际结果记录在对应版本
[milestones/](../milestones/) 的 DELIVERY。
