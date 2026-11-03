# V0.3 Rehost Workflow

Rehost 是 V0.3 的自动阶段。用户只提交 workspace 和目标描述，主 workflow 自动创建 harness、运行验证、处理失败并继续下一轮；用户不需要手动调用 rehost 命令。

## 主循环

```text
准备目标 SO 和共享源码
  ↓
收集静态检查、依赖、符号和材料快照
  ↓
创建空 ARM64 unidbg harness
  ↓
Rehost Worker 调查并修改 harness
  ↓
程序化 Verifier 构建并真实运行
  ↓
Checker Agent 只读审查证据
  ↓
分类结果
  ├─ harness/ABI/环境问题 → Rehost Worker
  ├─ 候选源码/语义问题 → Recovery/Differential Worker
  ├─ 固定契约或工具故障 → blocked，保存证据
  └─ 通过 → 释放给差分阶段
```

主 Manager 负责状态、轮次、锁、停止和恢复；不负责目标 ABI 推断。所有模型调用均通过 `agents.runtime.AgentTask`。

## 三个角色

**Manager**：`main_workflow` 创建或恢复 rehost task，保存状态，启动 Worker，等待 Verifier，依据验证结果决定下一轮。模型的完成声明不决定通过。

**Worker**：读取 rehost workflow、当前 harness、静态材料、上轮完整结果、验证报告和必要知识，调查并修改外部 harness。不能修改原始 SO、固定用例、独立 checker、共享源码或验证预期。

**Verifier**：程序化构建/运行和固定契约检查，再由只读 Checker Agent 审查完整证据。没有独立预期时只能返回 `observed`，不能宣称验证通过。

## Harness 生成阶段

1. 保存目标 SO 哈希和 workspace 关联。
2. 固定 `readelf`、依赖、符号和材料快照。
3. 在外部 workspace 创建空 Maven/unidbg 工程，不复制目标专用 Java 模板。
4. 生成 `HARNESS.md`，记录入口、ABI、初始化和待解决依赖。
5. Worker 先调查再编码；stub 必须有依据，不能用零/null 返回隐藏缺口。
6. Verifier 真实构建并执行；失败日志成为下一轮反馈。

## 数据边界

```text
source/src/             # 唯一当前 C 源码
knowledge.md            # Recovery 与差分共享知识
rehost/knowledge.md     # ABI、初始化、补环境和 harness 失败
harnesses/<target>/     # 当前可编辑 Java/unidbg 工程
tasks/<id>/             # prompt、返回、执行和状态
runs/<id>/              # 输入、构建、产物和验证快照
```

Rehost knowledge 可以引用共享 knowledge，但不能把未经验证的 harness 假设写成共享事实。差分发现的环境问题反馈 Rehost，源码问题反馈 Recovery。

## 失败分类

| 失败 | 处理 |
| --- | --- |
| 构建、加载、ABI、JNI 或系统调用失败 | Rehost Worker 修复工程或补环境 |
| 入口未命中、输出为空、初始化错误 | Rehost Worker 修正入口、初始化和观测点 |
| 候选与原实现不一致 | Differential Worker 修复共享源码；必要时反馈环境缺口 |
| 固定输入、checker 或 SO 哈希变化 | `blocked`，禁止修改基准 |
| 空返回、超时或中断 | 保存当前文件，恢复时重新验证 |

## 停止条件

- `complete`：固定契约通过，目标真实执行，Checker 审查通过。
- `observed`：取得真实观察但没有独立预期，只能作为差分输入。
- `blocked`：工具、权限或固定资源问题无法自动解决。
- `cancelled`：明确取消，保留当前 harness 和所有轮次。

恢复时重新核对 SO、harness、固定输入和保护文件，不重放仍在运行的 Agent，也不沿用旧通过报告。

## V0.3 交付顺序

1. AgentTask 接入 Rehost Worker 和 Checker。
2. 主 workflow 自动并行启动 Recovery 和 Rehost。
3. 完成空 harness、静态 setup、真实构建/运行和轮次恢复。
4. 接入失败分类和 Rehost ↔ 差分反馈。
5. 选一个 ARM64 目标完成从零生成、失败修复和可重放验收。

完整 VM 语义恢复、OLLVM/MBA 求解、fuzz 和强制执行不作为 Rehost 完成前置条件。
