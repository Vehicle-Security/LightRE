# V0.3.5：Rehost 终态判定与探针补环境

状态：已规划，待 V0.3 验收后开始。

V0.3 实测暴露的两个效率问题与本版本的结构性缺陷同源，合并为一个版本处理：

1. **观察到的停机条件缺陷**：observe 模式下"有真实执行"即 observed 停机，
   环境不完整也到站（见 [ROADMAP 整体规划](../../ROADMAP.md) 的 V0.3.5 行）。
   探针单独上线会让此问题恶化——兜底值让 harness 更容易"活着跑完"，更早误判完成。
2. **轮次效率**（2026-11-03 实测）：agent 每撞一个 JNI 缺口需要一轮重跑；
   大目标（真实字节码 + 多入口）30 分钟/轮不够，超时后下一轮重推理性，
   最坏每轮浪费 30 分钟。

## 功能一：终态判定改造（缺口清零才算 observed）

- `runner.run()` 从 observation.json 提取 `missing_environment` 与 `mode` 写入运行记录。
- `_conclude()`（workflow.py）的 observed 判定改为：真实执行 **且**
  `missing_environment` 为空 **且** 运行模式为自然运行。
- 观察协议同步：TASK.md 的 observation 模板已含 `missing_environment` 字段，
  明确其为终态判定输入，不完整清单视为未清零。

## 功能二：ProbeJni 通用探针（一次运行暴露全部缺口）

- workflow 模板新增通用 Java 支持类（零目标知识）：动态代理 `Jni` 接口，
  AbstractJni 抛"未实现"时按返回类型反射兜底
  （void 跳过 / 数值 0 / boolean false / String "" / 对象按类名建空壳 DvmObject），
  每次兜底记录完整签名、调用位置、兜底值。
- 工程内一次探针运行即可拿到完整缺口地图，agent 下一轮对单补真实现，
  预期把"逐坑摩"收敛为"测绘一次 + 补全一轮"。
- 探针只做调查；观察输出标 `"mode":"probe"`。

## 功能三：探索运行标记（probe/fuzz 不算通过）

- `runner.run()` 识别 observation 的 `"mode":"probe"`：运行状态不得为
  observed/passed，记录标注"干预实验"。
- 这是 V0.4 分支触发（fuzz）的前置语义：探索运行与自然运行永久区分
  （[DEOBFUSCATION](../../future/DEOBFUSCATION.md) F0）。

## 功能四：轮次超时策略

- 保留 1800s 默认；`lightre-rehost --timeout` 已可调。
- 增加按目标体量调整的指引与默认值评估：单轮内多目标（真实字节码+多入口）
  建议拆轮描述而非无限加大超时；超时续轮的损失（重推理性）写入
  HARNESS.md 模板的工作顺序建议（每轮目标单值化）。

## 验收

| 检查项 | 标准 |
| --- | --- |
| 停机条件 | observation 带非空 `missing_environment` 的运行不产生 observed 终态 |
| 探针 | 探针运行在验证中被标注且不算 observed/passed |
| 收敛效率 | 同一推进目标，探针版轮次少于无探针版（对比实测） |
| 回归 | 211 个既有测试通过；V0.3 的 4 字节码验收用例仍 observed |

## 范围外

- 分支触发/fuzz（V0.4，只做其依赖的标记语义）
- 语义等价验证（V0.4 差分）
