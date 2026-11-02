## 目标适配：DG（JNI，显式 headless-v1 fixture）

工程归属原 DG workspace 的 `harnesses/dg/`。DG 需要 JNI：使用 Android ARM64、
API23 / Unicorn2 / unidbg 0.9.9，建立 Dalvik VM，实际执行 JNI_OnLoad 并保存 RegisterNatives。
当前模板调用类 com/google/ccc/abuse/droidguard/DroidGuard 的实例方法，不能当作静态方法调用。
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
