# V0.1 使用说明：先导出，再浏览

V0.1 提供独立分析导出与只读 Web 浏览，最终交付范围和现场记录见 [DELIVERY.md](DELIVERY.md)。分析入口调用 Rizin，把函数及完整原始汇编保存到 workspace；Web 仅以只读方式打开其中的 SQLite。启动 Web、选择函数、搜索和滚动追加均不会调用 Rizin，也不要求 SO 或 Rizin 在浏览环境中可用。下文的 `/work2` 是本地示例路径，其他环境替换为实际路径。

## 聊天入口

```text
读取 /work2/LightRE/SKILL.md，分析 /work2/dg-samples/dC6BDAE，然后打开结果。
```

已有导出结果时，可以只说：

```text
用 LightRE 打开 /work2/dC6BDAE-analysis/dC6BDAE-d65f 的分析结果。
```

Agent 负责执行导出、检查成功、启动 Web，并返回结果目录与 URL。同一目标可复用服务，换目标时由 Skill 重启服务。用户不需要输入终端命令。

默认在会话工作目录下使用 `<名称>-analysis`；`.so` 取所在目录名，其他二进制取文件名去掉扩展名。重复打开复用该 workspace 中相同 SHA256 的结果，导出流程或工具版本变化时更新结果。工作区规则由 [SKILL.md](../../../SKILL.md) 统一说明；分析入口拒绝向工具仓库和样本 Git 仓库写入结果。

## 两个独立入口

分析导出使用系统 Python 和 Rizin，不依赖 Web 框架：

```bash
python3 /work2/LightRE/scripts/analyze.py \
  /work2/dg-samples/dC6BDAE/libdC6BDAE49562F.so \
  --workspace /work2/dC6BDAE-analysis
```

命令完成后返回 SO 分析目录。已有同 SHA256、导出流程及工具版本的完整结果时复用；`--force` 明确要求重新导出。旧 V0.1 索引会由此入口补齐汇编，Web 不迁移索引。

浏览只接受保存的分析目录：

```bash
/work2/LightRE/tools/venv/bin/python /work2/LightRE/scripts/web.py \
  /work2/dC6BDAE-analysis/dC6BDAE-d65f \
  --host 0.0.0.0 --port 8000
```

外层 workspace 仅有一个 SO 结果时也可直接传外层目录；多个结果时需明确选定 SO 分析目录。不存在或未完成导出的目录会报错，不创建数据库、不自动分析。容器外访问仍需映射端口。

Web 只浏览启动时指定的 SO 分析目录。要查看其他目标，告诉 Agent 按 Skill 打开该目标，Agent 重启服务并返回首页地址。

主题切换使用太阳/月亮图标，悬停提示目标主题；保留键盘操作与可访问名称，不再显示常驻的“工作区已加载”。

页面采用紧凑两栏布局。左侧显示 RVA、NAME 和 LINES，默认按行数降序，点击 RVA 或 LINES 表头切换排序。三列随左栏宽度按 1:1.4:1 分配空间，RVA 和 LINES 有最小列宽，LINES 表头与行数居中；长名称省略，悬停查看完整名称。

拖动两栏之间的分隔线调节左栏，默认 240px，最小 220px，最大 400px，窄视口进一步收紧上限。浏览器保存宽度，双击分隔线恢复默认；聚焦分隔线后可用左右方向键微调，Home/End 到达最小/最大宽度。汇编向下滚动时自动追加，底部仅显示紧凑进度栏，失败时才显示重试按钮。

## 结果与代码位置

每个 SO 的 `index.sqlite` 保存：

| 表 | 内容 |
| --- | --- |
| `metadata` | 原始身份、显示名称、来源、基址、结果格式、导出流程及 Rizin 版本 |
| `functions` | 函数 RVA、名称、完整行数和所属地址区间 |
| `assembly` | 函数 RVA、指令序号、指令 RVA、机器码、原始 opcode |

`assembly` 的主键是 `(function_rva, ordinal)`。Web 按指令序号分页读取，每次最多 200 条；同一条指令可能属于不同的工具识别函数。所有内容都是已导出的分析结果，不是临时缓存。

函数和汇编在同一事务中整体替换，已消失的函数及其汇编一起删除。导出失败会回滚，保留上一份完整结果，并在元数据的 `export_error` 记录错误；成功后清除该错误。初次失败的目录不能作为完整结果打开。

```text
src/
  analyzers/rizin.py    # 显式调用 Rizin、分析并导出
  workspace.py          # 分析入口准备目录；WorkspaceReader 只读已保存结果
  webui/app.py          # HTTP 只调用 WorkspaceReader
  webui/index.html
scripts/
  analyze.py            # 分析导出入口
  web.py                # 只读浏览入口
```

直接 Python 调用：

```python
from pathlib import Path
# 将 <LightRE>/src 加入 sys.path 后：
from analyzers.rizin import analyze
directory = analyze(Path('/输入/libexample.so'), Path('/外部/workspace'))

from workspace import WorkspaceReader
reader = WorkspaceReader(directory)
catalog = reader.catalog()
function = catalog['functions'][0]
page = reader.assembly(function['rva'], offset=0, limit=200)
```

HTTP 响应字段沿用 V0.1。名称/RVA 搜索、LINES 排序、浅色/深色与滚动自动加载保持可用。导出新结果后刷新页面即可查看；自动发布通知、伪代码、LLM 还原与动态分析留待后续小步。

`GET /api/functions` 返回启动目录的函数列表，`GET /api/functions/{rva}/assembly` 按 `offset`、`limit` 分页读取汇编。接口固定使用启动目录。

## 两个 workspace 的启动快捷指令

- [dg-samples SO](../../../tests/dg_start.md)：`/work2/dC6BDAE-analysis`。
- [minilua](../../../tests/minilua_start.md)：`/work2/minilua-analysis`。

每份 Markdown 只需交给 Agent，即可按 Skill 打开样本并返回 Web 地址，便于人工查看；不附加测试套件。

开发时保留关键数据行为的快速测试：

```bash
python3 -m unittest discover -s tests -v
```

这些测试使用临时文件和模拟分析结果，检查工作区复用、只读、分页、导出替换和失败回滚；不启动 Web、Rizin 或浏览器，不依赖真实 workspace。代码与界面主要由人工审阅，日常启动不运行这些开发测试。
