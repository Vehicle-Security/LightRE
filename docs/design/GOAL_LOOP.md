# Goal Loop 设计

**状态**: 长期设计文档，定义自动化逆向的核心循环

本文维护 Goal Loop 的设计原则、交接协议和停止条件。具体实现进度见各版本 PLAN，当前实现见 V0.3。

---

## 核心概念

Goal Loop 是 LightRE 实现长期自动化逆向的核心机制：

```
用户提交目标
  ↓
Goal Loop 循环:
  1. 验证当前状态
  2. 发现缺口
  3. 调用 Agent 修复
  4. 保存结果
  ↓ 重复
完成验收 → 交付
```

---

## 设计原则

### 1. 单 Agent 自主工作

每轮修复是**一个 Codex 会话**，内部自主：
- 观察代码和错误
- 规划修复步骤
- 修改多个文件
- 运行测试验证
- 看到失败继续改
- 完成后总结

**不需要多角色**（Planner/Executor/Checker）。

### 2. 程序化验证

验证由**真实测试**决定，不依赖 Agent 判断：

```python
def verify():
    # 1. 编译
    build_result = subprocess.run(['make'], ...)
    
    # 2. 运行测试
    test_result = subprocess.run(['make', 'test'], ...)
    
    # 3. 返回结果
    return {
        'passed': build_passed and tests_passed,
        'errors': [...],
        'logs': [...]
    }
```

### 3. 显式交接

每轮结果**显式保存**，下一轮**显式读取**：

```
round-0001/
  ├── prompt.md          # 本轮输入
  ├── result.md          # Agent 返回
  ├── execution.json     # 执行记录
  └── verification.json  # 验证结果

round-0002/
  ├── prompt.md          # 引用 round-0001 的结果
  └── ...
```

不依赖 Agent 内部记忆或上下文压缩。

---

## V0.3 实现范围

### ✅ 已实现

**rehost/workflow.py** - 单 Agent 修复循环（`run_rounds` / `_round_loop`，V0.3.5 已并入 workflow.py）：

```python
def run_rounds(directory, *, task=None, description=None, max_rounds=3, ...):
    # 创建或恢复任务后进入轮次循环
    while rounds < max_rounds:
        # 1. 验证（终态判定：passed / observed[缺口清零] / error）
        verification = _check_harness(...)
        if _conclude(state, verification, rounds, min_rounds):
            return state

        # 2. 单 Agent 自主工作
        result = run_blocking(
            root=directory / 'agents',
            directory=round_dir,
            cwd=harness,
            prompt=compose_prompt(...),   # 含知识库概览与上轮记录
            timeout=1800
        )

        # 3. 验证结果（超时但 Java 源码有实质变化视为进展）
        verification = _check_harness(...)
```

**特点**：
- 单 Agent 架构
- 程序化验证
- 轮次记录
- 基于 agents 框架

### ❌ 未实现（后续）

- 持久指导会话
- 主 workflow 自动编排
- Recovery + Rehost + 差分的完整循环
- VM 语义恢复
- 动态差分

---

## 完整 Goal Loop（未来）

### 架构愿景

```
┌─────────────────────────────────────┐
│  持久指导会话（用户交互）              │
│  - 接收目标                          │
│  - 报告进度                          │
│  - 调整策略                          │
└──────────────┬──────────────────────┘
               │ 编排
               ↓
┌─────────────────────────────────────┐
│  主 Workflow                         │
│  ├─ Recovery: 静态分析 → 源码恢复    │
│  ├─ Rehost: 生成 harness → 修复      │
│  └─ 差分: 候选构建 → 语义验证         │
└──────────────┬──────────────────────┘
               │ 调用
               ↓
┌─────────────────────────────────────┐
│  agents/ - 统一 Codex 执行           │
└─────────────────────────────────────┘
```

### 交接协议

1. **结果保存**：完整的 result.md、execution.json、verification.json
2. **下轮输入**：显式引用上轮结果，不依赖记忆
3. **停止条件**：程序化验证通过 + Agent 确认完成

### 知识管理

- `knowledge.md` - Recovery 共享知识
- `rehost/knowledge.md` - Rehost 专用知识
- 每轮 result.md - 本轮发现

---

## 参考

- **当前实现**: [V0.3 PLAN](../../milestones/v0.3/PLAN.md)
- **总体架构**: [ARCHITECTURE.md](ARCHITECTURE.md)
- **agents 详解**: [AGENTS_ARCHITECTURE_EXPLAINED.md](AGENTS_ARCHITECTURE_EXPLAINED.md)
- **未来功能**: [future/](../future/)

---

**Goal Loop 是 LightRE 的长期目标，V0.3 实现了基础循环。**
