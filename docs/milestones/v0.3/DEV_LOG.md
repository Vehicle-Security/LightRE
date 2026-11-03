# V0.3 阶段 B 开发日志

**目标**: DG SO 完整 Rehost - 自动生成 harness，unidbg 跑起来

**开始时间**: 2024-11-03

---

## Day 1: 环境准备和基础测试

### 任务清单

- [ ] 1.1 确认 DG SO 位置和版本
- [ ] 1.2 准备外部 workspace
- [ ] 1.3 配置 agents root
- [ ] 1.4 测试 rehost.start() 基本调用
- [ ] 1.5 查看生成的初始 harness 结构

### 执行记录

#### 1.1 DG SO 位置
- 根据 dg.md，DG 在原 workspace 的某个位置
- SHA: d65fc4e4...
- 需要确认具体路径

#### 问题
**需要用户提供**:
1. DG SO 文件的完整路径
2. 或者：是否需要重新分析 DG SO？

---

## 待办事项

### 环境准备
```bash
# 1. 准备 workspace
mkdir -p /tmp/dg-rehost-workspace

# 2. 配置 agents
export LIGHTRE_AGENTS_ROOT="$HOME/.lightre/agents"

# 3. 配置 Codex CLI
codex auth status
```

### 第一次运行
```python
from rehost import start

result = start(
    target='/path/to/dg.so',
    workspace='/tmp/dg-rehost-workspace',
    description='DG JNI rehost - JNI_OnLoad, RegisterNatives, initNative',
    max_rounds=5,
    timeout=1800  # 30分钟
)
```

### 预期输出
- harness 目录创建
- pom.xml 生成
- src/main/java/ 初始结构
- round-0001/ 记录

---

## 下一步

等待用户提供 DG SO 路径后开始执行。
