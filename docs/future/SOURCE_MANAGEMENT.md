# 源码管理和符号迁移

**状态**: 规划中，排期 V0.4

本文档记录源码管理和符号迁移的设计思路。代码已在 V0.3 删除，未来需要时从 git 历史恢复。

---

## 功能概述

### 1. SourceManager
统一的源码和符号管理接口

**核心功能**:
- 使用 RVA 作为函数主键
- 支持增量更新
- 符号重命名
- 源码同步

**API 示例**:
```python
from source_management import SourceManager

mgr = SourceManager('app.so', 'app.c')
mgr.load_source()
mgr.rename_function(0x401000, 'main')
mgr.sync_to_source()
```

### 2. FunctionInfo
函数信息数据类

**字段**:
- `rva`: 函数相对虚拟地址（主键）
- `symbol`: 函数符号名
- `code`: 反编译代码
- `start_line/end_line`: 源文件中的位置
- `metadata`: 额外元数据

### 3. SourceNavigator
源码导航和解析工具

**功能**:
- 从反编译 C 源码提取函数信息
- 查找函数边界
- 解析 RVA 注释

**示例**:
```python
navigator = SourceNavigator()
functions = navigator.parse_source(source_content)
```

### 4. SymbolMigrator
符号迁移工具（用于版本升级）

**用途**:
- 旧版本 → 新版本的符号迁移
- RVA 映射
- 函数匹配

---

## 使用场景

### Recovery 集成
```python
# 1. 管理恢复的源码
mgr = SourceManager(so_path, source_path)

# 2. 更新函数
mgr.update_function(rva, new_code)

# 3. 同步到文件
mgr.sync_to_source()
```

### 符号迁移
```python
# SO 版本升级时迁移符号
migrator = SymbolMigrator(
    old_binary='v1.so',
    new_binary='v2.so',
    old_source='v1.c',
    new_source='v2.c'
)
migrator.migrate()
```

---

## 设计原则

1. **RVA 作为主键** - 稳定的函数标识
2. **增量更新** - 只修改变化的函数
3. **源码同步** - 保持文件和内存一致
4. **可扩展元数据** - 支持自定义信息

---

## 实现细节

### 源码格式
```c
// Address: 0x401000
int main(int argc, char** argv) {
    // ...
}

// Address: 0x401100
void helper() {
    // ...
}
```

### RVA 注释解析
```python
# 匹配: // Address: 0x401000
RVA_PATTERN = re.compile(r'//\s*Address:\s*0x([0-9a-fA-F]+)')
```

### 函数边界检测
- 查找 RVA 注释
- 向后扫描函数签名
- 匹配大括号确定范围

---

## 与现有模块的关系

### recovery/
- **当前**: recovery 自己管理源码（candidates.py）
- **未来**: 可以使用 SourceManager 统一管理

### analyzers/
- **当前**: Ghidra 直接输出源码
- **未来**: 通过 SourceManager 管理符号

---

## V0.4 实现计划

### 阶段 1: 基础功能
- [ ] 恢复 SourceManager
- [ ] 集成到 recovery
- [ ] 基本的符号管理

### 阶段 2: 高级功能
- [ ] SymbolMigrator
- [ ] 版本升级支持
- [ ] 符号冲突处理

### 阶段 3: 优化
- [ ] 性能优化
- [ ] 大文件支持
- [ ] 增量同步

---

## 代码位置（已删除）

### 原位置
- `src/source_management/source_manager.py` (340行)
- `src/source_management/source_navigator.py` (178行)
- `src/source_management/function_info.py` (45行)
- `src/analyzers/migration.py` (SymbolMigrator)

### 恢复方法
```bash
# 从 git 历史恢复
git log --all --full-history -- "src/source_management/*"
git checkout <commit> -- src/source_management/
```

---

## 参考

- [SOURCE_NAVIGATION.md](../design/SOURCE_NAVIGATION.md) - 源码导航设计
- [V0.4 计划](../milestones/v0.4/PLAN.md)（待创建）

---

**排期 V0.4，当前专注 Rehost！**
