# LightRE 主 Workflow

主 Workflow 描述 Recovery、Rehost、候选构建和差分之间的长期自动推进关系。当前代码已经有最小入口 `src/lightre/main_workflow.py`，但完整的检查点、差分反馈和指导会话仍未交付。

```text
启动已完成静态导出的 workspace
  ├─ Recovery：函数恢复、源码和共享 Knowledge
  └─ Rehost：调查 SO、生成 unidbg harness 和补环境 Knowledge

Harness 达到可运行状态
  → 构建候选源码
  → 原实现/候选执行与差分
  → 保存失败、反例和证据
  → harness 问题反馈 Rehost
  → 源码或语义问题反馈 Recovery
  → 继续或暂停
```

## 当前实现

`lightre.server` 读取 workspace 后调用 `start_workflow()`。它会写入 `main-workflow.json`，提交 Recovery 任务，并在后台启动一次 Rehost。Recovery 任务仍由旧批次队列维护，Rehost 任务由 `rehost.workflow` 维护；两者还没有统一的 Worker、Verifier 和恢复协议。

## 后续边界

- 主服务重启时按检查点恢复已有任务，不重复创建 Rehost 工程。
- 把 Recovery、Rehost 和未来差分的模型调用统一接入 `agents`。
- 由确定性的 Verifier 决定 `complete`、`observed`、`failed` 和 `blocked`。
- 保存候选构建、双边运行、差异和反例，并将失败分类为 harness、环境、源码或语义问题。
- 指导会话只解释状态并提交明确任务，不直接修改业务完成状态。
