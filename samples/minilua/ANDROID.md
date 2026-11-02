# Android ARM64 / unidbg 适配

在本源码项目维护薄 C 接口 `src/unidbg_api.c` 和构建脚本 `scripts/build_android.py`。原 Linux 样本及构建方式保留；Android SO 是独立目标。

```bash
# 从 LightRE 根目录运行；系统依赖只有此安装入口。
bash scripts/install_deps.sh --android
tools/venv/bin/python samples/minilua/scripts/build_android.py \
  --output /absolute/original-workspace/fixtures/android-new --obfuscation small
tools/venv/bin/python scripts/rehost.py start \
  /absolute/original-workspace/fixtures/android-new/libminilua.so \
  --workspace /absolute/original-workspace \
  --goal '调查薄 C 接口，执行 Lua 输入并采集实际状态、输出和环境缺口' \
  --input samples/minilua/tests/smoke.lua --material samples/minilua/ANDROID.md
```

构建输出目录必须不存在，避免覆盖已有产物。可选 none/small/large；small 仅对 luaH_get/luaH_set 平坦化，large 对原大函数补丁的 luaV_execute split 后两轮平坦化。以实际 build.json 的工具链、哈希和 pass 日志为准。

`rehost start` 导入 SO，在原 workspace 的 harnesses 下创建独立工程，先生成 HARNESS.md，再由模型调查并编写 Java。此目标不创建 Dalvik VM，不需要 JNI；workspace 不另建可编辑 C 源码副本。上述命令仅给一个种子，运行成功也只报告 observed、passed=false。历史八例固定预期检查需要显式提供独立用例 JSON 和检查器，不能由种子运行推断通过；新生成流程尚未完成真实模型/unidbg 集成验证。

ABI：`lightre_minilua_run(source,length,chunk,argument)` 每次新建/关闭 Lua 状态，返回实际 Lua 状态；result/length 读取 stdout、stderr 与完整 Proto，vm_entry 提供真实 luaV_execute hook 地址。串行调用，输入/每类输出最多 1MiB，容量或 ABI 错误返回负数。完整 Java ABI 见 [专用说明](../../examples/minilua/ADAPTATION.md)，通用命令见 [功能说明](../../docs/USAGE.md#harness-配置与运行)，真实 small/large 证据见 [交付记录](../../docs/milestones/v0.31/DELIVERY.md)。
