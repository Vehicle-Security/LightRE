# 源码管理与符号迁移

状态：规划中，V0.4。相关代码已随 V0.3 重构删除，需要时从 git 历史恢复
（原 `src/source_management/` 与 `migration.py`，含 SourceManager、FunctionInfo、
SourceNavigator、SymbolMigrator 四个组件的设计与实现）。

## 定位

差分闭环（[DIFFERENTIAL_EXECUTION](DIFFERENTIAL_EXECUTION.md)）需要候选源码的
构建与执行；本模块提供其源码侧基础：

- **单一数据源**：维护一份候选源码，RVA 为主键，增量更新
- **符号管理**：函数重命名、RVA 映射、版本间符号迁移（旧 SO → 新 SO 的函数对应）
- **源码解析**：从候选 C 源码提取函数边界与 RVA 注释，支撑源码导航（见
  [SOURCE_NAVIGATION](../design/SOURCE_NAVIGATION.md)）与差分用例的入口定位

## 与其他功能的关系

- 候选构建产物与构建 Run 的保存约定在 DIFFERENTIAL_EXECUTION 维护，本文不重复
- 候选差分发现的语义错误 → 修源码 → 重建 → 再差分，是本模块的主要消费场景
- 恢复侧（Recovery）的静态候选输出是本模块的输入来源
