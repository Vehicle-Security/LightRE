# LightRE 函数还原任务

SO 结果目录：{{DIRECTORY}}

函数 RVA：{{RVAS}}

输入模式：{{INPUT_MODE}}（assembly 仅使用汇编与原 ELF，不读取 Ghidra 伪代码）。

## 用户意见

{{ COMMENT or '完成全部指定函数的一轮还原。' }}

提交建议时的候选 SHA256：{{ EXPECTED_SHA256 or '尚无候选' }}

执行时读取最新候选。若与提交时不同，结合 reviewed.c 理解评论，不回滚新结果。
