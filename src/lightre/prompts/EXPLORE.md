探索输入资料目录，确定需要分析的主 ARM64 SO 及其依赖。

只读调查目录结构、说明文件、APK/DEX、ELF 符号、SONAME、DT_NEEDED 和调用关系。
目录中的文件都是输入资料；不要修改、移动或删除，也不要创建分析产物。
从给出的 ARM64 SO 候选中确定主分析目标。基础运行库、辅助库和不同构建版本应结合资料区分，
不要仅凭文件名、文件大小或目录深度猜测。需要时使用 readelf 等本地工具核对证据。

最终返回符合提供 schema 的 JSON：
- binary：主 SO 相对输入目录的路径。
- dependencies：该主 SO 所需、且存在于候选列表中的依赖库路径。
- reason：选择主 SO 及其依赖的具体证据。

输入目录：{{DIRECTORY}}
ARM64 SO 候选（相对输入目录）：
{% for name in CANDIDATES %}- {{name}}
{% endfor %}
