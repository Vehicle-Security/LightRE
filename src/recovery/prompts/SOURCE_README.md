# 还原源码

src/ 按 Ghidra 分组组织多函数 C 模块，每个文件最多 {{FUNCTIONS_PER_FILE}} 个函数。
LightRE 根据 @lightre.begin/end JSON 注释定位函数；保留 ID 与 origins。
Web 隐藏机器定位和生产者注释，保留代码正文及普通注释。
可以编辑或移动模块；旧格式仍可读取，保存时转换该函数的标记。
SQLite source_index 是派生索引，文件变化后自动重建。

这些仍是未经执行验证的分析候选。共享类型、外部接口和构建方式尚待验证与整理，
不代表整个工程已经可编译或与原 SO 等价。
