# unidbg 补环境配方

unidbg API 用法、环境建模与常见陷阱的复用经验。条目格式见 [INDEX.md](INDEX.md)。

## 条目格式示例（发布真实经验时删除本节）

## 示例：JNI 方法返回 Context 时 AbstractJni 报不支持

- 症状：`UNIMPLEMENTED_JNI callObjectMethodv ...`，进程失败
- 原因：AbstractJni 默认不实现该方法
- 解法：在 AbstractJni 子类按签名覆写并返回 DvmObject；注明假设来源
- 来源：示例条目，非真实经验

<!-- 真实经验追加在此行之下 -->
