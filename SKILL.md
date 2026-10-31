# LightRE：先导出分析结果，再打开 Web

这是项目内的 Agent 使用说明，直接读取即可，无需全局安装。先遵循本目录的 AGENTS.md。

当前 V0.1 支持 ARM64 ELF 的函数与完整汇编导出，以及工作区的只读浏览。尚未实现反编译、LLM 自动还原、unidbg 或 Web 评论。

用户可以要求分析 SO/目录，也可以只打开已有 workspace。按输入决定执行以下步骤：

1. **确认目标。** 二进制文件路径直接使用，支持 `.so` 和无扩展名的 ARM64 ELF；输入目录优先查看直接 `.so`，多个无法区分的候选才询问，跳过包含 `index.sqlite` 的生成目录。只分析选定目标，不自动导入参考版本、重建或修改输入文件。若用户给的是已导出结果，直接进入浏览步骤，不要求原始 SO 或 Rizin 存在。
2. **仅在需要分析时准备外部 workspace。** 先捕获会话工作目录，不因临时切到工具目录而改变位置。尊重用户指定的位置；未指定时使用会话工作目录下的 `<名称>-analysis`：`.so` 取所在目录名，其他二进制取文件名去掉扩展名。会话目录为 `/work2` 时，`dC6BDAE/libdC6BDAE49562F.so` 默认使用 `/work2/dC6BDAE-analysis`，`deliverable/minilua` 默认使用 `/work2/minilua-analysis`。重复打开使用同一 workspace，不为测试或每次调用另建场景目录；保留其中其他版本的结果。分析产物不得放进 LightRE 工具仓库或样本 Git 仓库，分析入口会拒绝这些位置。若会话目录本身位于这些仓库内，先选择仓库外的位置。创建前说明路径，不需重复确认。Rizin 使用项目 `tools/rizin/bin/rizin` 或 PATH 安装；不要求先装 Ghidra。
3. **显式导出后再浏览。** 调用下述分析入口，等待成功并取得返回的 SO 分析目录。它保存全部函数与汇编；已有相同身份、导出流程和工具版本的完整结果时复用，需要重新分析才加 `--force`。失败时报告错误，不把旧结果说成本次新分析成功。

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

函数数量与边界是工具识别结果，不保证完整或正确。Web 从 SQLite 只读加载，按 LINES 排序，支持名称/RVA 搜索、浅色/深色与滚动自动追加，失败后显示重试按钮。整个浏览过程不会执行 Rizin、读取 SO 或重建索引。显式导出新结果后刷新页面查看。

若直接统计与导出不一致，核对原始 SHA256、工具版本、分析命令与 `-H RZ_PREFIX`。项目自带 Rizin 以 portable 模式构建，`-H RZ_IS_PORTABLE` 应为 `1`，避免目录改名后丢失分析数据。

API、持久结果和开发命令见 [V0.1 使用说明](docs/milestones/v0.1/USAGE.md)。
