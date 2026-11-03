# 未来功能设计

本目录存放未来功能的设计文档，这些功能尚未实现或仍在规划中。

## 文档列表

### 动态差分
- **[DIFFERENTIAL_EXECUTION.md](DIFFERENTIAL_EXECUTION.md)** - ARM64 unidbg 动态差分
  - 状态: 尚未实现
  - 版本: V0.4+
  - 内容: 双边差分设计、状态映射、候选构建

### VM 恢复
- **[VM_RECOVERY.md](VM_RECOVERY.md)** - VM 分析方法与语义恢复里程碑
  - 状态: 长期方法，未交付
  - 版本: V0.5+
  - 内容: M0-M6 里程碑、控制流平坦化、MBA 混淆

### 主循环
- **[MAIN_WORKFLOW.md](MAIN_WORKFLOW.md)** - 主 Workflow 自动推进
  - 状态: 规划中
  - 版本: V0.4+
  - 内容: Recovery、Rehost、差分的顶层循环

### 源码管理
- **[SOURCE_MANAGEMENT.md](SOURCE_MANAGEMENT.md)** - 源码管理和符号迁移
  - 状态: 规划中，已删除代码
  - 版本: V0.4
  - 内容: SourceManager、FunctionInfo、SymbolMigrator

## 与当前版本的关系

尚未接入执行器的 [工程恢复模板](prompts/RECOVER_AND_REFINE.md) 和 [构建与验证模板](prompts/BUILD_AND_VERIFY.md) 保存在 `prompts/`，仅供未来设计参考，不参与当前运行时打包。

**当前版本**: V0.3 - 彻底搞定 Rehost

这些文档描述的是后续版本的功能，不影响当前开发。需要实现时再移回 docs/design/。

## 版本规划

| 功能 | 版本 | 优先级 |
|-----|------|--------|
| 源码管理 | V0.4 | 高 |
| 动态差分 | V0.4 | 高 |
| 主循环 | V0.4 | 中 |
| VM 恢复 | V0.5+ | 低 |

---

**参考**: [V0.3 设计文档](../design/)

