# LightRE：先导出分析结果，再打开 Web

这是项目内的 Agent 使用说明，直接读取即可，无需全局安装。先遵循本目录的 AGENTS.md。

当前 V0.2 支持 ARM64 ELF 的函数与完整汇编导出、Agent 保存单函数 C 候选，以及工作区的只读浏览。候选始终标为“未验证”；尚未实现自动批量还原、unidbg 或 Web 评论。

用户可以要求分析 SO/目录，也可以只打开已有 workspace。按输入决定执行以下步骤：

1. **确认目标。** 二进制文件路径直接使用，支持 `.so` 和无扩展名的 ARM64 ELF；输入目录优先查看直接 `.so`，多个无法区分的候选才询问，跳过包含 `index.sqlite` 的生成目录。只分析选定目标，不自动导入参考版本、重建或修改输入文件。若用户给的是已导出结果，直接进入浏览步骤，不要求原始 SO 或 Rizin 存在。
2. **仅在需要分析时准备外部 workspace。** 先捕获会话工作目录，不因临时切到工具目录而改变位置。尊重用户指定的位置；未指定时使用会话工作目录下的 `<名称>-analysis`：`.so` 取所在目录名，其他二进制取文件名去掉扩展名。会话目录为 `/work2` 时，`dC6BDAE/libdC6BDAE49562F.so` 默认使用 `/work2/dC6BDAE-analysis`，`deliverable/minilua` 默认使用 `/work2/minilua-analysis`。重复打开使用同一 workspace，不为测试或每次调用另建场景目录；保留其中其他版本的结果。分析产物不得放进 LightRE 工具仓库或样本 Git 仓库，分析入口会拒绝这些位置。若会话目录本身位于这些仓库内，先选择仓库外的位置。创建前说明路径，不需重复确认。Rizin 使用项目 `tools/rizin/bin/rizin` 或 PATH 安装；不要求先装 Ghidra。
3. **显式导出后再浏览。** 调用下述分析入口，等待成功并取得返回的 SO 分析目录。它保存全部函数与汇编；已有相同身份、导出流程和工具版本的完整结果时复用，需要重新分析才加 `--force`。失败时报告错误，不把旧结果说成本次新分析成功。

   首次分析若缺少 Rizin，运行 `bash <LightRE>/scripts/install_deps.sh --rizin-only`；V0.2 不需要 Ghidra 或 Java。默认不 source `set_env.sh`，它会检查完整工具链。

   ```bash
   python3 <LightRE>/scripts/analyze.py <SO或输入目录> --workspace <外部workspace>
   ```

4. **打开已保存的结果。** Web 只需 FastAPI 和 Uvicorn。项目没有 Python 环境时，执行 `python3 -m venv <LightRE>/tools/venv`，然后用该环境的 Python 执行 `-m pip install -r <LightRE>/requirements.txt`；Ubuntu 缺少 venv 时安装 `python3-venv`。仅浏览时不安装或启动分析工具。
5. **启动或重启 Web。** 每个 Web 进程只打开启动时指定的 SO 分析目录，页面不提供工作区切换。已有服务时，请求 `/api/functions` 核对 `sha256` 和 `directory`；目标相同可复用，目标不同时，确认进程属于本项目的 `scripts/web.py`，停止该进程并在原端口打开目标目录。不要停止无关服务；端口由其他程序占用时选择空闲端口。以独立后台进程启动下述入口，保留日志；容器默认监听 `0.0.0.0`。日志可放在外部 workspace 的 `web.log`，返回普通首页 URL 即可。

   ```bash
   <LightRE>/tools/venv/bin/python <LightRE>/scripts/web.py <SO分析目录> \
     --host 0.0.0.0 --port <空闲端口>
   ```

   SO 分析目录应包含 `index.sqlite`。外层 workspace 只有一个 SO 结果时也可传外层目录；多个版本时明确选择一个。Web 对未导出或旧格式结果报错，不自动调用 Rizin；由当前 Agent 返回分析步骤处理。
6. **确认启动并交付。** 简单请求首页和函数列表，确认服务可用、目标正确，返回实际 URL、workspace 和 SO 结果目录即可。日常打开不运行测试套件，不安装浏览器测试依赖；界面由用户人工查看。容器外访问需要端口映射，不能把容器内检查说成外部已可达。留下服务供验收，不自动进入下一阶段。

## 单函数还原与人工反馈

用户要求还原或修改函数时，当前 Agent 读取该 SO 保存的函数汇编，编写可读 C 候选，明确尚未解决的语义和假设，不必启动另一个 LLM 会话。Python API 见 [V0.2 使用说明](docs/milestones/v0.2/USAGE.md)。

将候选写入外部临时文件后，用以下入口保存。它检查函数 RVA，自动添加当前 binary 文件名、SHA256、RVA 和未验证注释，并原子替换当前候选：

```bash
python3 <LightRE>/scripts/candidate.py save <SO分析目录> <RVA> --file <候选C文件>
python3 <LightRE>/scripts/candidate.py read <SO分析目录> <RVA>
```

每个函数只有 `candidates/0x<RVA补齐8位>.c` 这一份当前候选，正文不写入 SQLite。收到意见后，先读取当前文件再修改，保留来源注释；可直接编辑该文件，也可再次通过保存入口发布。Web 从同一文件读取，点击“还原代码”或刷新即可看到更新。返回 `http://<地址>:<端口>/?view=candidate#0x<RVA>` 可直接定位候选，刷新后保留视图和函数。

用户请求 **V0.2 minilua 示例**时，使用仓库内的 `samples/minilua/deliverable/minilua`，按正常流程导出；目标为 `0x23314`（15 条指令）。若该函数尚无候选，再用 `samples/minilua/candidates/0x00023314.c` 作为 `--file` 保存；已有候选时保留人工或 Agent 的修改。示例只适用于该文件 SHA256，不能复制到 large 版或其他 SO。返回候选视图 URL，留给人工对照。其他没有候选的函数显示“尚未还原”。

函数数量与边界是工具识别结果，不保证完整或正确。Web 从 SQLite 只读加载，按 LINES 排序，支持名称/RVA 搜索、浅色/深色与滚动自动追加，失败后显示重试按钮。整个浏览过程不会执行 Rizin、读取 SO 或重建索引。显式导出新结果后刷新页面查看。

若直接统计与导出不一致，核对原始 SHA256、工具版本、分析命令与 `-H RZ_PREFIX`。项目自带 Rizin 以 portable 模式构建，`-H RZ_IS_PORTABLE` 应为 `1`，避免目录改名后丢失分析数据。

API、持久结果和开发命令见 [V0.2 使用说明](docs/milestones/v0.2/USAGE.md)，原始汇编功能见 [V0.1 使用说明](docs/milestones/v0.1/USAGE.md)。
