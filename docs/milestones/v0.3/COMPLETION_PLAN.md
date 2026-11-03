# V0.3 完成计划

**目标**: 彻底搞定 Rehost - 从 SO 自动生成可运行的 unidbg harness

**当前状态**: 阶段 A 完成（Agent Runtime 收敛），准备开始阶段 B

---

## 阶段 B: Rehost 完整实现

### 步骤 1: 验证基础功能（1-2天）

**目标**: 确保 rehost 基础代码可用

**任务**:
```bash
# 1.1 测试 workflow.start()
uv run python -c "
from rehost import start
# 测试创建 workspace 和初始 harness
"

# 1.2 测试 loop.repair()
uv run python -c "
from rehost.loop import repair
# 测试修复循环
"

# 1.3 运行现有测试
uv run python tests/test_execution.py
uv run python tests/test_rehost.py
```

**预期产出**:
- [ ] start() 能创建 harness 目录
- [ ] repair() 能调用 agents
- [ ] 现有测试通过或明确失败原因

---

### 步骤 2: 端到端集成（3-5天）

**目标**: 一个真实 SO 走通完整流程

**选择最简单的 SO** (smoke):
```bash
# 2.1 准备外部 workspace
mkdir -p /tmp/rehost-workspace
cp samples/smoke.so /tmp/rehost-workspace/

# 2.2 运行 rehost
uv run python -c "
from rehost import start

result = start(
    target='/tmp/rehost-workspace/smoke.so',
    workspace='/tmp/rehost-workspace',
    description='Simple smoke test SO',
    max_rounds=3,
    timeout=1800
)
print(result)
"
```

**需要解决的问题**:
- [ ] Codex CLI 配置和登录
- [ ] agents root 目录设置
- [ ] unidbg 环境准备
- [ ] Maven 依赖安装

**预期产出**:
- [ ] 能生成初始 harness 目录结构
- [ ] 修复循环运行（即使未通过所有测试）
- [ ] 完整日志可查（prompt.md, result.md, execution.json）

---

### 步骤 3: 修复和优化（5-7天）

**目标**: 修复循环真正能自动修复错误

**迭代改进**:

1. **改进 prompt**
   - `src/rehost/prompts/REPAIR_HARNESS.md`
   - 更清晰的错误提示
   - 提供修复建议和示例

2. **改进验证**
   - `src/rehost/runner.py`
   - 更详细的错误信息
   - 分离编译错误、链接错误、运行时错误

3. **改进循环**
   - `src/rehost/loop.py`
   - 更好的超时处理
   - 失败重试策略
   - 增量修复（不重复已修复的问题）

**测试用例**:
- [ ] 编译错误（缺少头文件） → 自动添加 import
- [ ] 链接错误（缺少库） → 自动修改 pom.xml
- [ ] 运行时错误（空指针） → 自动添加检查
- [ ] 缺少依赖（JNI hook） → 自动实现

---

### 步骤 4: 多 SO 验证（3-5天）

**目标**: 至少 3 个 SO 通过完整流程

**测试矩阵**:

| SO | 复杂度 | 函数数 | 预期结果 | 状态 |
|----|--------|--------|----------|------|
| smoke | 简单 | ~5 | 全部通过 | ⏳ |
| minilua | 中等 | ~20 | 部分通过 | ⏳ |
| DG | 复杂 | ~50 | 基本运行 | ⏳ |

**每个 SO 的验收**:
- [ ] 自动生成 harness（pom.xml, src/main/java/）
- [ ] 修复循环运行完成（不超时、不崩溃）
- [ ] 至少 1 个测试用例通过
- [ ] 记录失败原因和改进方向

---

### 步骤 5: 文档和示例（2-3天）

**文档清单**:

1. **使用文档**
   - `docs/USAGE.md` - 更新 rehost 部分
   - 完整的命令行示例
   - 参数说明

2. **故障排查**
   - `docs/TROUBLESHOOTING.md`
   - 常见错误和解决方法
   - 环境配置问题

3. **示例**
   - `docs/examples/rehost-smoke.md` - smoke SO 完整示例
   - `docs/examples/rehost-minilua.md` - minilua SO 示例
   - `docs/examples/common-errors.md` - 常见错误集

**示例内容**:
```markdown
# Rehost Smoke 示例

## 1. 准备
mkdir -p /tmp/workspace
cp samples/smoke.so /tmp/workspace/

## 2. 运行
from rehost import start
result = start(...)

## 3. 查看结果
ls /tmp/workspace/harnesses/smoke-rehost-xxx/

## 4. 故障排查
...
```

---

## 时间估计

| 步骤 | 预计时间 | 累计时间 |
|-----|---------|---------|
| 1. 验证基础功能 | 1-2天 | 2天 |
| 2. 端到端集成 | 3-5天 | 7天 |
| 3. 修复和优化 | 5-7天 | 14天 |
| 4. 多 SO 验证 | 3-5天 | 19天 |
| 5. 文档和示例 | 2-3天 | 22天 |

**总计**: 2-3 周

---

## 关键里程碑

### 第 1 周
- ✅ 验证基础功能
- ✅ smoke SO 走通端到端
- ⏳ 开始修复优化

### 第 2 周
- ✅ 修复循环能自动处理常见错误
- ✅ minilua SO 走通
- ⏳ 开始 DG SO

### 第 3 周
- ✅ 3 个 SO 验证完成
- ✅ 文档齐全
- ✅ V0.3 验收通过

---

## 需要的资源

### 1. 外部 workspace
```bash
/tmp/rehost-workspace/  # 或用户指定路径
├── smoke.so
├── minilua.so
├── DG.so
└── harnesses/
    ├── smoke-rehost-xxx/
    └── ...
```

### 2. Codex CLI
```bash
# 已安装
codex --version

# 已登录
codex auth status

# 配置（可选）
export LIGHTRE_CODEX_PROFILE=default
```

### 3. unidbg 环境
```bash
# Java
java -version  # JDK 11+

# Maven
mvn -version

# unidbg 依赖
# 在 pom.xml 中配置
```

### 4. 测试 SO
```bash
samples/
├── smoke.so        # 简单测试
├── minilua.so      # 中等复杂度
└── DG/             # 真实目标
```

---

## 风险和应对

| 风险 | 概率 | 影响 | 应对策略 |
|------|------|------|----------|
| Agent 无法自动修复复杂错误 | 高 | 中 | 先完成简单 SO，复杂的接受部分失败 |
| unidbg 环境配置问题 | 中 | 高 | 提前准备 Docker 镜像，文档详细说明 |
| 超时问题（30分钟不够） | 中 | 中 | 调整 timeout 到 60 分钟，分步验证 |
| 依赖缺失（Maven/Java） | 低 | 中 | 文档记录依赖清单，提供安装脚本 |
| 真实 SO 太复杂 | 中 | 低 | 降低验收标准（部分通过即可） |

---

## 验收标准

### 最低标准（必须）
- [ ] 给定 ARM64 SO，能自动生成初始 harness
- [ ] 修复循环能运行完成（不崩溃）
- [ ] 至少 1 个简单 SO（smoke）完全通过
- [ ] 文档齐全，可复现

### 理想标准（期望）
- [ ] 3 个 SO 都能生成 harness
- [ ] 修复循环能自动处理常见错误
- [ ] minilua 至少部分测试通过
- [ ] DG 能基本运行

### 超出标准（可选）
- [ ] 所有 SO 所有测试通过
- [ ] 修复循环完全自动化
- [ ] 性能优化（更快的修复）

---

## 下一步行动

1. **立即开始** - 步骤 1：验证基础功能
2. **创建测试脚本** - 自动化验证流程
3. **准备环境** - 外部 workspace、Codex CLI
4. **第一个 milestone** - smoke SO 走通

**从验证基础功能开始！** 🚀
