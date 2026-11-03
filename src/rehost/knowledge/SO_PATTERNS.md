# SO 通用模式

Android ARM64 SO 的加载、初始化与 JNI 注册等通用模式经验。条目格式见 [INDEX.md](INDEX.md)。

覆盖的典型问题：JNI_OnLoad 行为、RegisterNatives 时机、初始化依赖链、
反调试/完整性检查、libc 接口缺口、线程与回调建模。

<!-- 真实经验追加在此行之下 -->
