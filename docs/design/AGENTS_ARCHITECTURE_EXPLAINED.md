# Agent 架构

`agents` 是 LightRE 的 Codex CLI 执行层。它是内部运行库，不是产品命令入口，也不负责逆向任务的业务判断。

## 组件

```text
AgentManager
  ├─ AgentSession       持久会话和消息队列
  ├─ AgentRun           单条消息的可恢复执行句柄
  ├─ agents.sqlite      session/run 状态、租约和取消请求
  └─ worker.py          每个 session 的后台 Codex owner

codex.py               命令构造、事件读取和进程组控制
```

`AgentManager` 可以在任意进程中按 ID 重建句柄。句柄只依赖 agents root、session ID 和 run ID，不持有不可序列化的进程对象。

## 生命周期

1. `manager.create(cwd, ...)` 创建 session 并固定 Codex 可执行文件、sandbox、model、profile 和禁用功能。
2. `session.submit(message)` 将一条消息写入 SQLite 和 run 目录，并按需启动 worker。
3. worker 以 FIFO 顺序认领 queued run，写入 prompt、事件和执行记录。
4. `AgentRun.poll()` 读取持久状态；`iter_states()` 只在状态变化时产生更新；`wait()` 在同步边界等待终态。
5. 正常结束、失败、取消、超时和 worker 意外退出都写入终态；无法确认完成时标记为 `interrupted`，不会自动重放。

## 数据

```text
<agents-root>/
  agents.sqlite
  sessions/<session-id>/
    worker.log
    runs/<run-id>/
      prompt.md
      request.json
      schema.json
      events.jsonl
      stderr.log
      result.txt
      execution.json
```

SQLite 保存生命周期状态，文件保存模型调用原文和诊断材料。业务模块只保存自己的任务状态，并引用 session/run ID；模型是否完成业务目标必须由业务验证器判断。

## 公开 Python 接口

```python
from agents import AgentManager

manager = AgentManager('/workspace/agents')
session = manager.create('/workspace/project')
run = session.submit('调查并修复当前问题', request_id='repair-1')

for state in run.iter_states(timeout=600):
    print(state['status'])
result = run.result()
```

常用接口是：

- `AgentSession.submit()` / `send_message()`：非阻塞提交下一轮消息。
- `AgentRun.poll()`：读取当前持久状态。
- `AgentRun.iter_states()`：迭代状态变化直到终态。
- `AgentRun.wait()`：同步等待并返回最终结果。
- `AgentRun.cancel()`：取消排队或正在运行的消息。
- `AgentSession.pause()`、`resume()`、`cancel()`：控制同一会话的队列。

实时 steering 当前不支持；运行中的消息按 `next_turn` 排队。

## 业务接入

- Rehost 修复循环使用 `agents.run_blocking()`，需要把 Agent 结果转换为 Rehost 的轮次记录。
- Recovery 仍使用自己的批次调度器和结果 schema，尚未迁移到 Agent runtime。
- 未来主服务会把指导会话和后台工作会话都登记在该层，但指导消息与业务任务不会共享同一份状态。

统一迁移完成前，不要在 Recovery、Rehost 或服务层再创建第三套 Codex subprocess 管理器。
