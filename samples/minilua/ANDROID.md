# Android ARM64 / unidbg 适配

在本源码项目维护薄 C 接口 `src/unidbg_api.c` 和构建脚本 `scripts/build_android.py`。原 Linux 样本及构建方式保留；Android SO 是独立目标。

```bash
# 从 LightRE 根目录运行；系统依赖只有此安装入口。
bash scripts/install_deps.sh --android
tools/venv/bin/python samples/minilua/scripts/build_android.py \
  --output /absolute/original-workspace/fixtures/android-new --obfuscation small
tools/venv/bin/python scripts/harness.py configure \
  /absolute/original-workspace/fixtures/android-new/libminilua.so \
  --workspace /absolute/original-workspace --kind minilua
```

构建输出目录必须不存在，避免覆盖已有产物。可选 none/small/large；small 仅对 luaH_get/luaH_set 平坦化，large 对原大函数补丁的 luaV_execute split 后两轮平坦化。以实际 build.json 的工具链、哈希和 pass 日志为准。

`configure` 打印导入的 SO 子目录，先生成 Java 工程的 HARNESS.md，关联后通过 `harness.py check <SO目录>` 运行仓库 8 个 Lua 用例。Java 使用原 workspace 中的 `harnesses/minilua/`，不创建 Dalvik VM，不需要 JNI；workspace 不另建可编辑 C 源码副本。

ABI：`lightre_minilua_run(source,length,chunk,argument)` 每次新建/关闭 Lua 状态，返回实际 Lua 状态；result/length 读取 stdout、stderr 与完整 Proto，vm_entry 提供真实 luaV_execute hook 地址。串行调用，输入/每类输出最多 1MiB，容量或 ABI 错误返回负数。完整签名、命令、真实 small/large 证据与限制见 [目标 harness](../../docs/TARGET_HARNESSES.md)。
