# V0.2 使用说明

V0.2 在 V0.1 的函数与汇编浏览上增加一份当前单函数 C 候选。候选由 Agent 分析后编写，保存在外部 workspace；Web 读取同一文件，始终标明“分析候选，未验证”。当前无需 Ghidra、unidbg 或数据库迁移。

## 新电脑 clone 后的人工入口

环境为 Linux，建议 Ubuntu 24.04；Python 3.10 或以上。静态读取 ARM64 ELF 不要求宿主机也是 ARM64。

```bash
git clone https://github.com/Vehicle-Security/LightRE.git
```

在 clone 所在目录启动 Agent，将下面这句交给它即可：

```text
读取 LightRE/SKILL.md，分析 LightRE/samples/minilua/deliverable/minilua，打开 V0.2 minilua 单函数候选示例供我查看。
```

也可以直接交给 Agent [tests/minilua_start.md](../../../tests/minilua_start.md)。这是现成的启动快捷指令，不会触发测试套件。

Agent 会准备依赖、在会话工作目录创建或复用 `minilua-analysis/`，导出当前样本，将配套候选保存到选定 SO 的分析目录，并启动 Web。候选已存在时保留当前修改，不用示例覆盖。返回的地址形如：

```text
http://localhost:8000/?view=candidate#0x00023314
```

在容器里运行时需要映射端口，远程机器访问使用该机器的实际地址。仓库内置 minilua 样本和候选，不依赖另一份 dg-samples 仓库或旧机器上的 workspace。

## 界面与人工反馈

- 左栏搜索 `0x23314`：这是小函数版 minilua 的 15 条指令函数。
- 点击“汇编 / 还原代码”切换，候选页显示未验证状态、实际文件路径和完整文件正文。
- 选中没有候选的函数，显示“尚未还原”，其汇编仍可查看。
- 给 Agent 提出意见，例如“说明循环里的辅助函数调用和重新读取字段的关系”。Agent 修改当前候选后，点击“还原代码”重新读取，或刷新页面。
- URL 保存当前函数与候选视图。刷新、重启服务或换 Agent 会话后，仍读取原文件。

示例候选根据已保存的汇编编写，字段 `runtime`、`pending` 是暂定名称，辅助函数的业务语义仍未知。它没有经过原函数与候选的执行比较。

## Agent / 开发调用

以下 `<LightRE>` 和 `<SO分析目录>` 替换为实际绝对路径；这些命令由 Skill 的执行 Agent 负责处理。

仅首次分析需要 Rizin：

```bash
bash <LightRE>/scripts/install_deps.sh --rizin-only
python3 <LightRE>/scripts/analyze.py <目标文件> --workspace <外部workspace>
```

Web 依赖：

```bash
python3 -m venv <LightRE>/tools/venv
<LightRE>/tools/venv/bin/python -m pip install -r <LightRE>/requirements.txt
```

缺少 venv 时先安装 `python3-venv`。`--rizin-only` 跳过 Ghidra 与 Java；仅浏览已有结果不需要任何分析工具。日常启动无需 source 会检查完整工具链的 `set_env.sh`。

先从 `WorkspaceReader.assembly()` 读取目标函数的汇编，再由 Agent 编写候选。将正文写入外部临时文件，通过以下命令保存；来源注释由保存入口生成：

```bash
python3 <LightRE>/scripts/candidate.py save <SO分析目录> 0x23314 --file /外部/候选.c
python3 <LightRE>/scripts/candidate.py read <SO分析目录> 0x23314
```

配套 minilua 示例可直接导入：

```bash
python3 <LightRE>/scripts/candidate.py save <SO分析目录> 0x23314 \
  --file <LightRE>/samples/minilua/candidates/0x00023314.c
```

它只适用于 SHA256 为 `0432af2744d5f5fcc49d1acbb6c5ad0ff04d7cb62455f0ec2606e3420601e050` 的小函数版 minilua。携带其他 SO 或 RVA 来源注释的候选会被拒绝，不能把这个示例导入 large 版。

Web 启动保持原样：

```bash
<LightRE>/tools/venv/bin/python <LightRE>/scripts/web.py <SO分析目录> --port 8000
```

一个进程固定打开一份 SO 的结果；换目标时由 Skill 重启服务。

## 文件与 Python API

```text
<外部workspace>/<SO名称>-<SHA短码>/
  index.sqlite
  binary/original.so
  candidates/
    0x00023314.c
```

候选按 RVA 规范化为至少 8 位十六进制文件名；同一函数只有这一份当前候选，不增加历史表、缓存或正文副本。SQLite 仍保存原始函数和汇编索引，格式版本保持不变。

```python
from pathlib import Path
# 将 <LightRE>/src 加入 sys.path 后：
from workspace import WorkspaceReader
from candidates import read_candidate, save_candidate

reader = WorkspaceReader(Path('/外部workspace/目标结果'))
assembly = reader.assembly(0x23314)
path = save_candidate(reader, 0x23314, '/* Agent 编写的 C 候选正文 */\n')
candidate = read_candidate(reader, 0x23314)
assert candidate['code'] == path.read_text(encoding='utf-8')
```

直接修改候选文件也会立即影响下一次读取；保留文件顶部的来源注释。保存入口可接受纯正文或来源匹配的完整候选文件，不会重复添加注释。写入采用临时文件加原子替换，失败保留旧文件。读取和保存均不调用分析工具、不修改 SQLite。

`GET /api/functions/{rva}/candidate` 返回 `rva`、`name`、`path`、`code`、`status`。无候选时 HTTP 200、`code=null`、`status=missing`；有候选时正文为文件原文、状态为 `unverified`。未知函数为 404，非法 RVA 为 400，来源注释不符为 409。现有函数列表和汇编分页 API 保持可用。

## 快速开发检查

```bash
python3 -m unittest discover -s tests -v
```

共 11 项数据层测试，使用临时文件与模拟数据，不启动 Web、Rizin 或浏览器。日常启动只确认服务和目标，界面由人工验收。
