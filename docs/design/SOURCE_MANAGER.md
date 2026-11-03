# Source Manager 设计文档

## 问题背景

当前 `migration.py` 中对符号和反编译源码的管理逻辑分散，不便于后续的 recover 模块和 rewrite（差分对比逆向）模块复用。需要抽象出统一的接口来管理：

1. 反编译源码的维护
2. 符号信息的管理
3. RVA 到函数的映射关系

## 设计目标

- **单一数据源**：始终维护一份 source，避免重复和不一致
- **RVA 为主键**：使用 RVA 作为函数的唯一标识符
- **接口统一**：为不同模块（migration、recover、rewrite）提供一致的访问接口
- **增量更新**：支持函数级别的增量更新，无需重新反编译整个二进制

## 核心组件

### 1. SourceManager

负责管理单个二进制文件的反编译源码和符号信息。

```python
class SourceManager:
    """管理单个二进制文件的反编译源码和符号信息"""
    
    def __init__(self, binary_path: str, source_path: Optional[str] = None):
        """
        Args:
            binary_path: 二进制文件路径
            source_path: 源码保存路径，默认为 binary_path.c
        """
        
    # 核心状态
    self.binary_path: str          # 二进制文件路径
    self.source_path: str          # 反编译源码文件路径
    self.functions: Dict[int, FunctionInfo]  # RVA -> FunctionInfo 映射
    self.symbols: Dict[str, int]   # 符号名 -> RVA 映射
    
    # 方法
    def load_source(self) -> str:
        """加载反编译源码"""
        
    def save_source(self, content: str):
        """保存反编译源码"""
        
    def get_function(self, rva: int) -> Optional[FunctionInfo]:
        """通过 RVA 获取函数信息"""
        
    def update_function(self, rva: int, code: str, symbol: Optional[str] = None):
        """更新函数代码和符号"""
        
    def add_function(self, rva: int, code: str, symbol: Optional[str] = None):
        """添加新函数"""
        
    def remove_function(self, rva: int):
        """移除函数"""
        
    def get_function_by_symbol(self, symbol: str) -> Optional[FunctionInfo]:
        """通过符号名获取函数"""
        
    def rename_function(self, rva: int, new_symbol: str):
        """重命名函数"""
        
    def sync_to_source(self):
        """将内存中的函数信息同步到源文件"""
```

### 2. FunctionInfo

封装单个函数的完整信息。

```python
@dataclass
class FunctionInfo:
    """函数信息"""
    rva: int                    # 函数 RVA（主键）
    symbol: str                 # 函数符号名
    code: str                   # 反编译代码
    start_line: int             # 在源文件中的起始行号
    end_line: int               # 在源文件中的结束行号
    metadata: Dict[str, Any]    # 额外元数据（参数、返回值等）
```

### 3. SourceNavigator

提供源码导航和解析能力，从 SOURCE_NAVIGATION.md 中抽取。

```python
class SourceNavigator:
    """源码导航和解析"""
    
    def parse_source(self, source_content: str) -> Dict[int, FunctionInfo]:
        """解析源码，提取所有函数信息"""
        
    def find_function_bounds(self, source_content: str, start_offset: int) -> Tuple[int, int]:
        """查找函数边界（起始行和结束行）"""
        
    def extract_function_rva(self, function_code: str) -> Optional[int]:
        """从函数代码中提取 RVA（从注释）"""
        
    def extract_symbol(self, function_code: str) -> str:
        """提取函数符号名"""
```

## 使用场景

### Migration（符号迁移）

```python
# 原有逻辑
source_mgr = SourceManager(new_binary_path)
source_mgr.load_source()

# 应用符号映射
for old_rva, new_rva in rva_mapping.items():
    old_func = old_source_mgr.get_function(old_rva)
    if old_func and old_func.symbol.startswith("meaningful_"):
        source_mgr.rename_function(new_rva, old_func.symbol)

source_mgr.sync_to_source()
```

### Recover（函数恢复）

```python
# 恢复某个函数
source_mgr = SourceManager(binary_path)
source_mgr.load_source()

# 重新反编译特定函数
new_code = decompile_function(binary_path, rva)
source_mgr.update_function(rva, new_code)

source_mgr.sync_to_source()
```

### Rewrite（差分对比逆向）

```python
# 对比两个版本
old_mgr = SourceManager(old_binary)
new_mgr = SourceManager(new_binary)

# 查找变化的函数
for old_rva, new_rva in rva_mapping.items():
    old_func = old_mgr.get_function(old_rva)
    new_func = new_mgr.get_function(new_rva)
    
    if old_func.code != new_func.code:
        print(f"函数 {old_func.symbol} 在 {old_rva:x} -> {new_rva:x} 发生变化")
        diff = compute_diff(old_func.code, new_func.code)
        # 处理差分
```

## 实现细节

### RVA 提取策略

从反编译代码注释中提取 RVA：

```c
// Address: 0x401000
void function_name() {
    // ...
}
```

正则表达式：`r'//\s*Address:\s*0x([0-9a-fA-F]+)'`

### 源码同步策略

1. **解析现有源码**：提取所有函数的 RVA、符号、行号
2. **构建内存映射**：`Dict[int, FunctionInfo]`
3. **修改操作**：在内存中更新函数信息
4. **回写源码**：
   - 保留文件头注释和全局声明
   - 按 RVA 排序输出所有函数
   - 每个函数前添加 RVA 注释

### 错误处理

- **RVA 冲突**：同一 RVA 有多个函数 → 抛出异常
- **符号冲突**：同一符号有多个 RVA → 使用最新的映射
- **源文件损坏**：无法解析 → 从二进制重新反编译

## 迁移计划

1. **Phase 1**：实现核心组件
   - `FunctionInfo` 数据类
   - `SourceNavigator` 解析器
   - `SourceManager` 基础实现

2. **Phase 2**：重构 migration.py
   - 使用 `SourceManager` 替代现有逻辑
   - 保持接口兼容

3. **Phase 3**：支持新模块
   - 实现 recover 命令
   - 实现 rewrite 对比功能

## 文件结构

```
src/
  core/
    source_manager.py      # SourceManager 实现
    source_navigator.py    # SourceNavigator 实现
    function_info.py       # FunctionInfo 数据类
  analyzers/
    migration.py           # 重构后使用 SourceManager
  commands/
    recover.py             # 新增：函数恢复
    rewrite.py             # 新增：差分对比
```

## 优势

1. **代码复用**：migration、recover、rewrite 共享同一套逻辑
2. **维护简单**：符号和源码管理集中在一处
3. **扩展性强**：容易添加新的元数据和操作
4. **一致性保证**：RVA 作为主键，避免混乱
5. **增量更新**：只需更新变化的函数，提高效率
