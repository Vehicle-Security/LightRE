## DG 历史适配记录（JNI，headless-v1 fixture）

以下记录 2026-11-01 已运行工程的 ABI、环境与限制，供调查和核对证据。当前新建目标通过 [rehost](../../docs/USAGE.md#rehost-从零生成) 从零生成 harness，不再通过样本模板或便捷配置脚本生成 Java。旧工程和 Run 保留在原 workspace；这些记录不能代替新流程的集成验证。

工程归属原 DG workspace 的 `harnesses/dg/`。DG 需要 JNI：使用 Android ARM64、
API23 / Unicorn2 / unidbg 0.9.9，建立 Dalvik VM，实际执行 JNI_OnLoad 并保存 RegisterNatives。
当时的工程调用类 com/google/ccc/abuse/droidguard/DroidGuard 的实例方法，不能当作静态方法调用。
每个新 SO 版本仍需核对实际注册、签名和初始化；共享工程不代表跨版本行为相同。

实际生命周期：JNI_OnLoad → new DroidGuard instance →
`initNative(Context,String,[B,Object,Bundle,II)J` → `ssNative(J,[String],Bundle)[B` →
`closeNative(J,[String])V`。完整 JNI 描述符见 Java 与实际 registrations。
flow=attest，callback=null，空 Bundle，尾部两个 int=0；返回 handle 必须 >0。
ss 的键为 dg_email、dg_package、dg_androidId；保存真实返回数组 output.bin 及 output_hex。
通过版本参数 vm_entry_rva 观察自然执行的核心入口，包括当前 SHA d65fc4e4… 的 0x1c07c；
不能以该 RVA 绕过初始化直接调用、强改 ready 状态或声称 VM 语义已恢复。

request 必须显式包含 environment_profile=headless-v1、runtime_gr（int）、runtime_i/runtime_j（long）、
runtime_h/runtime_m/runtime_k（string）、monotonic_nanos、wall_time_millis、random_seed。
这些 opaque runtime 回调的设备语义缺少本版 DEX 确认，返回值是 fixture 输入，不能当作真实 GMS 行为。
实际观测的 g(float[]) 和 n() 无返回值回调设为 capture-only，记录原始 float bits 和通知。
其 Java 业务副作用未实现，不能由当前运行结论推导已恢复。

headless-v1 环境固定：GMS 包名，versionCode=0，无 APK 签名/权限；普通应用 flags=0、uid=10000；
无传感器、显示器、输入设备和启用的 accessibility 服务；512MiB 总内存、256MiB available、32MiB threshold；
feature registry 仅 ram.low，空 settings/preferences/binder registry，无 persistent_data_block；
稀疏 Java 属性集，Build 设备字符串 unknown。Java 时钟每次 +1ms，Random 使用显式 seed。
只提供空内存 JKS KeyStore 和真实 SUN provider；AndroidKeyStore key generator 缺失时
产生 NoSuchProviderException，Socket 使用离线 NoRouteToHostException，由目标自身处理异常。
不生成硬件 attestation，不伪造签名证书。native 环境由固定 unidbg 版本提供，未宣称任意新用例完全确定。

未知 JNI 明确报错，不能用通用零/null stub 消除缺口。unidbg 0.9.9 throwException 前必须
addLocalObject(exception)，否则 ExceptionOccurred 返回未注册引用，后续 GetObjectClass 失败。
外部检查要求真实核心命中、有效 handle、非空 byte[]、close 完成、七条注册、无缺失 JNI 或模拟错误；
重放使用保存的源码/参数/输入/依赖，并校验输出字节文件哈希。通过仅适用于所选输入和上述 fixture。
Java 入口 `lightre.DgHarness SO request.json observation.json`，参数路径相对 request。

### 当前目标入口

从 LightRE 根目录复用原 SO 分析目录，传入匹配版本的输入和只读资料：

```bash
tools/venv/bin/python scripts/rehost.py start <原DG的SO分析目录> \
  --goal '调查 JNI 注册与初始化，实际运行输入并记录输出、环境假设与缺口' \
  --input <匹配版本的bin文件> --material <匹配版本的资料目录>
```

未知 SO 版本须先调查真实入口，不能套用本版 RVA。每次 start 在原 workspace 的 harnesses 下创建独立工程；续做同一任务使用 resume。上述命令没有独立预期，只能取得 observed、passed=false。已有固定用例时按[功能说明](../../docs/USAGE.md#harness-配置与运行)配置独立检查器；历史验证路径只在[交付记录](../../docs/milestones/v0.31/DELIVERY.md)维护。
