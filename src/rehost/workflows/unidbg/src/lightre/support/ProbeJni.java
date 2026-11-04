package lightre.support;

import com.github.unidbg.Emulator;
import com.github.unidbg.linux.android.dvm.AbstractJni;
import com.github.unidbg.linux.android.dvm.Jni;
import com.github.unidbg.linux.android.dvm.VM;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 通用 JNI 探针（零目标知识），双模式：
 *
 * <ul>
 *   <li>{@code probe}：AbstractJni 未实现的调用按返回类型反射兜底并记录缺口，
 *       一次运行暴露当前层全部缺口清单。只用于调查，观察必须标 {@code "mode":"probe"}，
 *       外层验证器不会把探针运行算作 observed/passed。</li>
 *   <li>{@code strict}：不兜底，未实现直接抛出——自然运行的默认行为，
 *       保证缺环境如实失败。</li>
 * </ul>
 *
 * harness 建议从 request.json 的 {@code mode} 字段选择模式；
 * 自然运行前必须把缺口替换为有来源依据的真实实现。
 * 缺口带 family 分类（system-service / field / callback / other），
 * 供按族批量补实现，而不是逐条孤立处理。
 */
public final class ProbeJni {

    private final List<Map<String, Object>> gaps = Collections.synchronizedList(new ArrayList<>());
    private final Emulator<?> emulator;
    private final boolean fallbackEnabled;

    private ProbeJni(Emulator<?> emulator, boolean fallbackEnabled) {
        this.emulator = emulator;
        this.fallbackEnabled = fallbackEnabled;
    }

    /** 包装 AbstractJni 委托。fallbackEnabled=false 时未实现调用原样抛出（strict）。 */
    public static ProbeResult wrap(Emulator<?> emulator, VM vm, AbstractJni delegate, boolean fallbackEnabled) {
        ProbeJni probe = new ProbeJni(emulator, fallbackEnabled);
        Jni proxied = (Jni) Proxy.newProxyInstance(Jni.class.getClassLoader(), new Class<?>[]{Jni.class},
                (proxy, method, args) -> {
                    try {
                        return method.invoke(delegate, args);
                    } catch (InvocationTargetException exception) {
                        Throwable cause = exception.getCause() != null ? exception.getCause() : exception;
                        Map<String, Object> gap = probe.gap(method, args, cause);
                        probe.gaps.add(gap);
                        if (!probe.fallbackEnabled) {
                            throw cause;  // strict：如实失败，缺口已记录。
                        }
                        return probe.fallback(method);
                    }
                });
        return new ProbeResult(proxied, probe.gaps);
    }

    /** 按返回类型给出最小兜底值；对象除 String 外返回 null，由后续真实实现替换。 */
    private Object fallback(Method method) {
        Class<?> type = method.getReturnType();
        if (type == Boolean.TYPE || type == Boolean.class) {
            return Boolean.FALSE;
        }
        if (type == Integer.TYPE || type == Integer.class) {
            return 0;
        }
        if (type == Long.TYPE || type == Long.class) {
            return 0L;
        }
        if (type == Float.TYPE || type == Float.class) {
            return 0F;
        }
        if (type == Double.TYPE || type == Double.class) {
            return 0D;
        }
        if (type == String.class) {
            return "";
        }
        return null;
    }

    /** 按调用形态给缺口分类：system-service / field / callback / other。 */
    private static String family(String signature) {
        if (signature.contains("getSystemService") || signature.contains("SystemService")) {
            return "system-service";
        }
        if (signature.contains("Field")) {
            return "field";
        }
        if (signature.contains("Method") || signature.contains("->")) {
            return "callback";
        }
        return "other";
    }

    private Map<String, Object> gap(Method method, Object[] args, Throwable cause) {
        Map<String, Object> gap = new LinkedHashMap<>();
        String signature = method.getName();
        gap.put("jni", signature);
        gap.put("family", family(signature + ' ' + Arrays.toString(args)));
        gap.put("args", truncate(Arrays.toString(args), 300));
        gap.put("error", truncate(String.valueOf(cause), 200));
        if (fallbackEnabled) {
            gap.put("probe_answer", String.valueOf(fallback(method)));
        }
        if (emulator != null) {
            try {
                gap.put("location", "PC=" + emulator.getContext().getPCPointer()
                        + " LR=" + emulator.getContext().getLRPointer());
            } catch (Throwable ignored) {
                // 上下文不可用时缺口仍有效，只是没有位置信息。
            }
        }
        return gap;
    }

    private static String truncate(String text, int limit) {
        return text.length() <= limit ? text : text.substring(0, limit) + "...";
    }

    /** 包装结果：代理 Jni 与可序列化的缺口清单（写入 observation.missing_environment）。 */
    public static final class ProbeResult {
        public final Jni jni;
        public final List<Map<String, Object>> gaps;

        ProbeResult(Jni jni, List<Map<String, Object>> gaps) {
            this.jni = jni;
            this.gaps = gaps;
        }
    }
}
