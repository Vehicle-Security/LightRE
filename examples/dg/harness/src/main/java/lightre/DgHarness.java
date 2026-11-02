package lightre;

import com.github.unidbg.AndroidEmulator;
import com.github.unidbg.Module;
import com.github.unidbg.arm.backend.Unicorn2Factory;
import com.github.unidbg.arm.backend.Backend;
import com.github.unidbg.arm.backend.CodeHook;
import com.github.unidbg.arm.backend.UnHook;
import com.github.unidbg.linux.android.AndroidEmulatorBuilder;
import com.github.unidbg.linux.android.AndroidResolver;
import com.github.unidbg.linux.android.dvm.*;
import com.github.unidbg.linux.android.dvm.array.*;
import com.github.unidbg.pointer.UnidbgPointer;
import com.google.gson.*;
import java.lang.reflect.Field;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.ConcurrentHashMap;

/** Target JNI registrations come from JNI_OnLoad, never another SO's RVA. */
public final class DgHarness extends AbstractJni {
    private final JsonArray calls = new JsonArray();
    private final JsonArray callbacks = new JsonArray();
    private final JsonArray missing = new JsonArray();
    private JsonObject request;
    private long clockTick;
    private long vmEntries;
    private long randomInstances;
    private DvmObject<?> applicationLoader;
    private DvmObject<?> preferences;
    private final ThreadLocal<Integer> trafficTag = ThreadLocal.withInitial(() -> -1);

    @Override
    public DvmObject<?> newObjectV(BaseVM vm, DvmClass type, String signature, VaList args) {
        calls.add(signature);
        if (signature.equals("java/util/concurrent/ConcurrentLinkedQueue-><init>()V"))
            return type.newObject(new ConcurrentLinkedQueue<DvmObject<?>>());
        if (signature.equals("java/util/concurrent/ConcurrentHashMap-><init>()V"))
            return type.newObject(new ConcurrentHashMap<Object, DvmObject<?>>());
        if (signature.equals("java/lang/Float-><init>(D)V"))
            return type.newObject((float) args.getDoubleArg(0));
        if (signature.equals("java/lang/Long-><init>(J)V"))
            return type.newObject(args.getLongArg(0));
        if (signature.equals("java/lang/Double-><init>(D)V"))
            return type.newObject(args.getDoubleArg(0));
        if (signature.equals("java/util/Random-><init>()V"))
            return type.newObject(new Random(request.get("random_seed").getAsLong() + randomInstances++));
        if (signature.equals("android/app/ActivityManager$MemoryInfo-><init>()V"))
            return type.newObject(new HashMap<String, Long>());
        if (signature.equals("java/util/TreeSet-><init>()V"))
            return type.newObject(new TreeSet<Object>());
        if (signature.equals("java/net/Socket-><init>(Ljava/lang/String;I)V")) {
            calls.add("offline fixture socket=" + args.getObjectArg(0).getValue() + ":" + args.getIntArg(1));
            DvmObject<?> error = vm.resolveClass("java/net/NoRouteToHostException",
                    vm.resolveClass("java/net/SocketException", vm.resolveClass("java/io/IOException",
                            vm.resolveClass("java/lang/Exception", vm.resolveClass("java/lang/Throwable")))))
                    .newObject(new java.net.NoRouteToHostException("Network unavailable in headless-v1"));
            vm.addLocalObject(error);
            vm.throwException(error);
            return null;
        }
        if (signature.equals("android/util/DisplayMetrics-><init>()V"))
            return type.newObject(null);
        try { return super.newObjectV(vm, type, signature, args); }
        catch (UnsupportedOperationException error) { missing.add(signature); throw error; }
    }

    @Override
    @SuppressWarnings("unchecked")
    public boolean callBooleanMethodV(BaseVM vm, DvmObject<?> object, String signature, VaList args) {
        calls.add(signature);
        if (signature.equals("java/util/concurrent/ConcurrentLinkedQueue->add(Ljava/lang/Object;)Z") ||
                signature.equals("java/util/concurrent/ConcurrentLinkedQueue->offer(Ljava/lang/Object;)Z"))
            return ((Queue<DvmObject<?>>) object.getValue()).offer(args.getObjectArg(0));
        if (signature.equals("java/util/Enumeration->hasMoreElements()Z"))
            return ((java.util.Enumeration<?>) object.getValue()).hasMoreElements();
        if (signature.equals("android/content/pm/PackageManager->hasSystemFeature(Ljava/lang/String;)Z")) {
            String feature = (String) args.getObjectArg(0).getValue();
            calls.add("fixture feature query=" + feature);
            return Set.of("android.hardware.ram.low").contains(feature);
        }
        if (signature.equals("java/security/KeyStore->containsAlias(Ljava/lang/String;)Z")) {
            try { return ((java.security.KeyStore) object.getValue())
                    .containsAlias((String) args.getObjectArg(0).getValue()); }
            catch (Exception error) { throw new IllegalStateException(error); }
        }
        if (signature.equals("java/util/TreeSet->add(Ljava/lang/Object;)Z"))
            return ((Set<Object>) object.getValue()).add(args.getObjectArg(0).getValue());
        if (signature.equals("java/util/TreeSet->isEmpty()Z"))
            return ((Set<?>) object.getValue()).isEmpty();
        if (signature.equals("java/util/Iterator->hasNext()Z"))
            return ((java.util.Iterator<?>) object.getValue()).hasNext();
        if (signature.equals("android/view/accessibility/AccessibilityManager->isEnabled()Z")) return false;
        try { return super.callBooleanMethodV(vm, object, signature, args); }
        catch (UnsupportedOperationException error) { missing.add(signature); throw error; }
    }

    @Override
    public int getStaticIntField(BaseVM vm, DvmClass type, String signature) {
        calls.add(signature);
        // Constants from Android Sensor.java, not arbitrary fallback values.
        Map<String, Integer> sensors = Map.of("TYPE_ACCELEROMETER", 1, "TYPE_MAGNETIC_FIELD", 2,
                "TYPE_GYROSCOPE", 4, "TYPE_LIGHT", 5, "TYPE_PRESSURE", 6,
                "TYPE_PROXIMITY", 8, "TYPE_GRAVITY", 9, "TYPE_LINEAR_ACCELERATION", 10,
                "TYPE_ROTATION_VECTOR", 11, "TYPE_ALL", -1);
        for (Map.Entry<String, Integer> entry : sensors.entrySet())
            if (signature.equals("android/hardware/Sensor->" + entry.getKey() + ":I")) return entry.getValue();
        if (signature.equals("android/os/Build$VERSION->SDK_INT:I")) return 23;
        if (signature.equals("android/view/Display->DEFAULT_DISPLAY:I")) return 0;
        if (signature.equals("android/content/pm/PackageManager->GET_PERMISSIONS:I")) return 0x1000;
        try { return super.getStaticIntField(vm, type, signature); }
        catch (UnsupportedOperationException error) { missing.add(signature); throw error; }
    }

    @Override
    public int getIntField(BaseVM vm, DvmObject<?> object, String signature) {
        calls.add(signature);
        if (signature.equals("android/content/pm/ApplicationInfo->flags:I") ||
                signature.equals("android/content/pm/PackageInfo->versionCode:I")) return 0;
        if (signature.equals("android/content/pm/ApplicationInfo->uid:I")) return 10000;
        try { return super.getIntField(vm, object, signature); }
        catch (UnsupportedOperationException error) { missing.add(signature); throw error; }
    }

    @Override
    public long getLongField(BaseVM vm, DvmObject<?> object, String signature) {
        calls.add(signature);
        for (String name : List.of("totalMem", "availMem", "threshold")) {
            if (signature.equals("android/app/ActivityManager$MemoryInfo->" + name + ":J")) {
                @SuppressWarnings("unchecked")
                Map<String, Long> info = (Map<String, Long>) object.getValue();
                return info.getOrDefault(name, 0L);
            }
        }
        try { return super.getLongField(vm, object, signature); }
        catch (UnsupportedOperationException error) { missing.add(signature); throw error; }
    }

    @Override
    public DvmObject<?> getObjectField(BaseVM vm, DvmObject<?> object, String signature) {
        calls.add(signature);
        if (signature.equals("android/content/pm/ApplicationInfo->deviceProtectedDataDir:Ljava/lang/String;"))
            return new StringObject(vm, "/data/user_de/0/com.google.android.gms");
        if (signature.equals("android/content/pm/ApplicationInfo->dataDir:Ljava/lang/String;"))
            return new StringObject(vm, "/data/user/0/com.google.android.gms");
        if (signature.equals("android/content/pm/PackageInfo->signatures:[Landroid/content/pm/Signature;") ||
                signature.equals("android/content/pm/PackageInfo->requestedPermissions:[Ljava/lang/String;")) return new ArrayObject();
        try { return super.getObjectField(vm, object, signature); }
        catch (UnsupportedOperationException error) { missing.add(signature); throw error; }
    }

    @Override
    public DvmObject<?> getStaticObjectField(BaseVM vm, DvmClass type, String signature) {
        calls.add(signature);
        if (signature.equals("android/content/pm/PackageManager->FEATURE_STRONGBOX_KEYSTORE:Ljava/lang/String;"))
            return new StringObject(vm, "android.hardware.strongbox_keystore");
        if (signature.equals("android/security/keystore/KeyProperties->KEY_ALGORITHM_EC:Ljava/lang/String;"))
            return new StringObject(vm, "EC");
        if (signature.equals("android/provider/Settings$Global->DEVELOPMENT_SETTINGS_ENABLED:Ljava/lang/String;"))
            return new StringObject(vm, "development_settings_enabled");
        if (signature.equals("android/provider/Settings$Global->ADB_ENABLED:Ljava/lang/String;"))
            return new StringObject(vm, "adb_enabled");
        if (signature.equals("android/provider/Settings$Global->BOOT_COUNT:Ljava/lang/String;"))
            return new StringObject(vm, "boot_count");
        if (signature.equals("android/content/Context->INPUT_SERVICE:Ljava/lang/String;"))
            return new StringObject(vm, "input");
        for (String name : List.of("BOARD", "BOOTLOADER", "BRAND", "DEVICE", "DISPLAY", "FINGERPRINT",
                "HARDWARE", "HOST", "ID", "MANUFACTURER", "MODEL", "PRODUCT", "RADIO", "SERIAL", "TAGS", "TYPE", "USER"))
            if (signature.equals("android/os/Build->" + name + ":Ljava/lang/String;"))
                return new StringObject(vm, "unknown");
        if (signature.equals("android/os/Build->CPU_ABI:Ljava/lang/String;")) return new StringObject(vm, "arm64-v8a");
        if (signature.equals("android/os/Build->CPU_ABI2:Ljava/lang/String;")) return new StringObject(vm, "");
        if (signature.equals("android/os/Build$VERSION->RELEASE:Ljava/lang/String;")) return new StringObject(vm, "6.0");
        if (signature.equals("android/os/Build$VERSION->SDK:Ljava/lang/String;")) return new StringObject(vm, "23");
        if (signature.equals("android/os/Build$VERSION->CODENAME:Ljava/lang/String;")) return new StringObject(vm, "REL");
        if (signature.equals("android/os/Build$VERSION->INCREMENTAL:Ljava/lang/String;") ||
                signature.equals("android/os/Build$VERSION->SECURITY_PATCH:Ljava/lang/String;")) return new StringObject(vm, "unknown");
        if (signature.equals("android/content/pm/PackageManager->FEATURE_VERIFIED_BOOT:Ljava/lang/String;"))
            return new StringObject(vm, "android.software.verified_boot");
        if (signature.equals("android/content/pm/PackageManager->FEATURE_RAM_NORMAL:Ljava/lang/String;"))
            return new StringObject(vm, "android.hardware.ram.normal");
        if (signature.equals("android/content/pm/PackageManager->FEATURE_RAM_LOW:Ljava/lang/String;"))
            return new StringObject(vm, "android.hardware.ram.low");
        try { return super.getStaticObjectField(vm, type, signature); }
        catch (UnsupportedOperationException error) { missing.add(signature); throw error; }
    }

    @Override
    public void callStaticVoidMethodV(BaseVM vm, DvmClass type, String signature, VaList args) {
        calls.add(signature);
        if (signature.equals("android/net/TrafficStats->setThreadStatsTag(I)V")) {
            trafficTag.set(args.getIntArg(0));
            return;
        }
        if (signature.equals("android/net/TrafficStats->clearThreadStatsTag()V")) {
            trafficTag.remove();
            return;
        }
        try { super.callStaticVoidMethodV(vm, type, signature, args); }
        catch (UnsupportedOperationException error) { missing.add(signature); throw error; }
    }

    @Override
    public int callStaticIntMethodV(BaseVM vm, DvmClass type, String signature, VaList args) {
        calls.add(signature);
        if (signature.equals("android/provider/Settings$Global->getInt(Landroid/content/ContentResolver;Ljava/lang/String;I)I")) {
            calls.add("empty settings key=" + args.getObjectArg(1).getValue() + " default=" + args.getIntArg(2));
            return args.getIntArg(2);
        }
        if (signature.equals("android/provider/Settings$Global->getInt(Landroid/content/ContentResolver;Ljava/lang/String;)I")) {
            String key = (String) args.getObjectArg(1).getValue();
            calls.add("missing settings key=" + key);
            DvmObject<?> error = vm.resolveClass("android/provider/Settings$SettingNotFoundException",
                    vm.resolveClass("java/lang/Exception", vm.resolveClass("java/lang/Throwable")))
                    .newObject(new Exception("Setting not found: " + key));
            vm.addLocalObject(error);
            vm.throwException(error);
            return 0;
        }
        if (signature.equals("android/net/TrafficStats->getThreadStatsTag()I")) return trafficTag.get();
        try { return super.callStaticIntMethodV(vm, type, signature, args); }
        catch (UnsupportedOperationException error) { missing.add(signature); throw error; }
    }

    @Override
    public long callStaticLongMethodV(BaseVM vm, DvmClass type, String signature, VaList args) {
        calls.add(signature);
        if (signature.equals("android/os/SystemClock->elapsedRealtimeNanos()J"))
            return request.get("monotonic_nanos").getAsLong() + clockTick++ * 1000000L;
        if (signature.equals("android/os/SystemClock->elapsedRealtime()J") ||
                signature.equals("android/os/SystemClock->uptimeMillis()J"))
            return request.get("monotonic_nanos").getAsLong() / 1000000L + clockTick++;
        if (signature.equals("java/lang/System->currentTimeMillis()J"))
            return request.get("wall_time_millis").getAsLong() + clockTick++;
        try { return super.callStaticLongMethodV(vm, type, signature, args); }
        catch (UnsupportedOperationException error) { missing.add(signature); throw error; }
    }

    @Override
    public DvmObject<?> callStaticObjectMethodV(BaseVM vm, DvmClass type, String signature, VaList args) {
        calls.add(signature);
        if (signature.equals("java/security/KeyStore->getInstance(Ljava/lang/String;)Ljava/security/KeyStore;") &&
                "AndroidKeyStore".equals(args.getObjectArg(0).getValue())) {
            calls.add("configured environment: empty in-memory KeyStore fixture, no hardware attestation");
            try {
                java.security.KeyStore store = java.security.KeyStore.getInstance("JKS");
                store.load(null, null);
                return type.newObject(store);
            } catch (Exception error) { throw new IllegalStateException(error); }
        }
        if (signature.equals("java/security/KeyPairGenerator->getInstance(Ljava/lang/String;Ljava/lang/String;)Ljava/security/KeyPairGenerator;") &&
                "AndroidKeyStore".equals(args.getObjectArg(1).getValue())) {
            calls.add("configured environment: no AndroidKeyStore key generator provider");
            DvmObject<?> error = vm.resolveClass("java/security/NoSuchProviderException",
                    vm.resolveClass("java/security/GeneralSecurityException", vm.resolveClass("java/lang/Exception", vm.resolveClass("java/lang/Throwable"))))
                    .newObject(new java.security.NoSuchProviderException("AndroidKeyStore is unavailable in headless-v1"));
            vm.addLocalObject(error);
            vm.throwException(error);
            return null;
        }
        if (signature.equals("java/lang/System->getProperty(Ljava/lang/String;)Ljava/lang/String;")) {
            String key = (String) args.getObjectArg(0).getValue();
            calls.add("system property=" + key);
            String value = Map.of("os.arch", "aarch64", "os.name", "Linux",
                    "java.vm.name", "LightRE.unidbg-fixture").get(key);
            return value == null ? null : new StringObject(vm, value);
        }
        if (signature.equals("android/provider/Settings$Global->getString(Landroid/content/ContentResolver;Ljava/lang/String;)Ljava/lang/String;")) {
            calls.add("empty settings string key=" + args.getObjectArg(1).getValue());
            return null;
        }
        if (signature.equals("android/os/ServiceManager->getService(Ljava/lang/String;)Landroid/os/IBinder;")) {
            calls.add("empty binder registry service=" + args.getObjectArg(0).getValue());
            return null;
        }
        if (signature.equals("android/preference/PreferenceManager->getDefaultSharedPreferences(Landroid/content/Context;)Landroid/content/SharedPreferences;")) {
            if (preferences == null) preferences = vm.resolveClass("android/content/SharedPreferences")
                    .newObject(new HashMap<String, Object>());
            return preferences;
        }
        if (signature.equals("java/security/Security->getProviders()[Ljava/security/Provider;"))
            return new ArrayObject(vm.resolveClass("java/security/Provider")
                    .newObject(java.security.Security.getProvider("SUN")));
        try { return super.callStaticObjectMethodV(vm, type, signature, args); }
        catch (UnsupportedOperationException error) { missing.add(signature); throw error; }
    }

    @Override
    public void callVoidMethodV(BaseVM vm, DvmObject<?> object, String signature, VaList args) {
        calls.add(signature);
        if (signature.equals("java/security/KeyStore->load(Ljava/security/KeyStore$LoadStoreParameter;)V") &&
                args.getObjectArg(0) == null) {
            try { ((java.security.KeyStore) object.getValue()).load(null); }
            catch (Exception error) { throw new IllegalStateException(error); }
            return;
        }
        if (signature.equals("android/app/ActivityManager->getMemoryInfo(Landroid/app/ActivityManager$MemoryInfo;)V")) {
            @SuppressWarnings("unchecked")
            Map<String, Long> info = (Map<String, Long>) args.getObjectArg(0).getValue();
            info.put("totalMem", 512L * 1024 * 1024);
            info.put("availMem", 256L * 1024 * 1024);
            info.put("threshold", 32L * 1024 * 1024);
            return;
        }
        if (signature.equals("com/google/ccc/abuse/droidguard/DroidGuard->g([F)V")) {
            float[] values = (float[]) args.getObjectArg(0).getValue();
            JsonArray bits = new JsonArray();
            for (float value : values) bits.add(String.format("%08x", Float.floatToRawIntBits(value)));
            JsonObject callback = new JsonObject();
            callback.addProperty("signature", signature);
            callback.add("float_bits", bits);
            callbacks.add(callback);
            return;
        }
        if (signature.equals("com/google/ccc/abuse/droidguard/DroidGuard->n()V")) {
            JsonObject callback = new JsonObject();
            callback.addProperty("signature", signature);
            callback.addProperty("policy", "capture-only");
            callbacks.add(callback);
            return;
        }
        try { super.callVoidMethodV(vm, object, signature, args); }
        catch (UnsupportedOperationException error) { missing.add(signature); throw error; }
    }

    @Override
    public long callLongMethodV(BaseVM vm, DvmObject<?> object, String signature, VaList args) {
        calls.add(signature);
        if (signature.equals("java/util/Random->nextLong()J"))
            return ((Random) object.getValue()).nextLong();
        if (signature.equals("com/google/ccc/abuse/droidguard/DroidGuard->i()J") && request.has("runtime_i")) {
            long value = request.get("runtime_i").getAsLong();
            calls.add("configured opaque runtime i=" + value);
            return value;
        }
        if (signature.equals("com/google/ccc/abuse/droidguard/DroidGuard->j()J") && request.has("runtime_j")) {
            long value = request.get("runtime_j").getAsLong();
            calls.add("configured opaque runtime j=" + value);
            return value;
        }
        try { return super.callLongMethodV(vm, object, signature, args); }
        catch (UnsupportedOperationException error) { missing.add(signature); throw error; }
    }

    @Override
    public int callIntMethodV(BaseVM vm, DvmObject<?> object, String signature, VaList args) {
        calls.add(signature);
        if (signature.equals("com/google/ccc/abuse/droidguard/DroidGuard->gr()I") && request.has("runtime_gr")) {
            int value = request.get("runtime_gr").getAsInt();
            calls.add("configured opaque runtime gr=" + value);
            return value;
        }
        if (signature.equals("java/security/KeyStore->size()I")) {
            try { return ((java.security.KeyStore) object.getValue()).size(); }
            catch (Exception error) { throw new IllegalStateException(error); }
        }
        if (signature.equals("java/util/Random->nextInt(I)I"))
            return ((Random) object.getValue()).nextInt(args.getIntArg(0));
        if (signature.equals("java/util/Random->nextInt()I"))
            return ((Random) object.getValue()).nextInt();
        if (signature.equals("java/util/TreeSet->size()I")) return ((Set<?>) object.getValue()).size();
        if (signature.equals("android/os/Bundle->getInt(Ljava/lang/String;I)I")) {
            calls.add("empty Bundle int=" + args.getObjectArg(0).getValue() + " default=" + args.getIntArg(1));
            return args.getIntArg(1);
        }
        try { return super.callIntMethodV(vm, object, signature, args); }
        catch (UnsupportedOperationException error) { missing.add(signature); throw error; }
    }

    @Override
    @SuppressWarnings("unchecked")
    public DvmObject<?> callObjectMethodV(BaseVM vm, DvmObject<?> object, String signature, VaList args) {
        calls.add(signature);
        // Android Bundle returns null for absent keys of these reference types.
        if (signature.equals("android/os/Bundle->getString(Ljava/lang/String;)Ljava/lang/String;") ||
                signature.equals("android/os/Bundle->getIntArray(Ljava/lang/String;)[I") ||
                signature.equals("android/os/Bundle->getByteArray(Ljava/lang/String;)[B") ||
                signature.equals("android/os/Bundle->getStringArray(Ljava/lang/String;)[Ljava/lang/String;")) {
            calls.add("empty Bundle key=" + args.getObjectArg(0).getValue());
            return null;
        }
        if (signature.endsWith("->getClass()Ljava/lang/Class;")) return object.getObjectType();
        if (object.getValue() instanceof Throwable) {
            Throwable error = (Throwable) object.getValue();
            if (signature.endsWith("->getCause()Ljava/lang/Throwable;"))
                return error.getCause() == null ? null : vm.resolveClass("java/lang/Throwable").newObject(error.getCause());
            if (signature.endsWith("->getMessage()Ljava/lang/String;"))
                return error.getMessage() == null ? null : new StringObject(vm, error.getMessage());
            if (signature.endsWith("->toString()Ljava/lang/String;"))
                return new StringObject(vm, object.getObjectType().getClassName().replace('/', '.') +
                        (error.getMessage() == null ? "" : ": " + error.getMessage()));
        }
        if (signature.equals("android/content/pm/PackageManager->getInstallerPackageName(Ljava/lang/String;)Ljava/lang/String;") &&
                "com.google.android.gms".equals(args.getObjectArg(0).getValue())) return null;
        if (signature.equals("com/google/ccc/abuse/droidguard/DroidGuard->h()Ljava/lang/String;") && request.has("runtime_h")) {
            String value = request.get("runtime_h").getAsString();
            calls.add("configured opaque runtime h=" + value);
            return new StringObject(vm, value);
        }
        if (signature.equals("com/google/ccc/abuse/droidguard/DroidGuard->m()Ljava/lang/String;") && request.has("runtime_m")) {
            String value = request.get("runtime_m").getAsString();
            calls.add("configured opaque runtime m=" + value);
            return new StringObject(vm, value);
        }
        if (signature.equals("com/google/ccc/abuse/droidguard/DroidGuard->k()Ljava/lang/String;") && request.has("runtime_k")) {
            String value = request.get("runtime_k").getAsString();
            calls.add("configured opaque runtime k=" + value);
            return new StringObject(vm, value);
        }
        switch (signature) {
            case "android/view/accessibility/AccessibilityManager->getEnabledAccessibilityServiceList(I)Ljava/util/List;":
            case "android/view/accessibility/AccessibilityManager->getInstalledAccessibilityServiceList()Ljava/util/List;":
                return vm.resolveClass("java/util/ArrayList", vm.resolveClass("java/util/List")).newObject(new ArrayList<>());
            case "java/security/Provider->getName()Ljava/lang/String;":
                return new StringObject(vm, ((java.security.Provider) object.getValue()).getName());
            case "java/security/Provider->getInfo()Ljava/lang/String;":
                return new StringObject(vm, ((java.security.Provider) object.getValue()).getInfo());
            case "java/security/Provider->toString()Ljava/lang/String;":
                return new StringObject(vm, object.getValue().toString());
            case "android/content/SharedPreferences->getAll()Ljava/util/Map;":
                return vm.resolveClass("java/util/Map").newObject(new HashMap<>((Map<?, ?>) object.getValue()));
            case "android/content/SharedPreferences->getString(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;":
                Object value = ((Map<?, ?>) object.getValue()).get(args.getObjectArg(0).getValue());
                return value == null ? args.getObjectArg(1) : new StringObject(vm, (String) value);
            case "android/hardware/input/InputManager->getInputDeviceIds()[I":
                return new IntArray(vm, new int[0]);
            case "java/util/TreeSet->iterator()Ljava/util/Iterator;":
                return vm.resolveClass("java/util/Iterator").newObject(((Set<?>) object.getValue()).iterator());
            case "java/util/Iterator->next()Ljava/lang/Object;":
                return com.github.unidbg.linux.android.dvm.jni.ProxyDvmObject.createObject(vm,
                        ((java.util.Iterator<?>) object.getValue()).next());
            case "java/util/TreeSet->toString()Ljava/lang/String;":
                return new StringObject(vm, object.getValue().toString());
            case "android/content/pm/PackageManager->getPackageInfo(Ljava/lang/String;I)Landroid/content/pm/PackageInfo;":
            case "android/content/pm/PackageManager->getApplicationInfo(Ljava/lang/String;I)Landroid/content/pm/ApplicationInfo;": {
                String packageName = (String) args.getObjectArg(0).getValue();
                calls.add("application info package=" + packageName);
                if (packageName.equals("com.google.android.gms"))
                    return vm.resolveClass(signature.contains("->getPackageInfo(") ?
                            "android/content/pm/PackageInfo" : "android/content/pm/ApplicationInfo").newObject(null);
                DvmObject<?> error = vm.resolveClass("android/content/pm/PackageManager$NameNotFoundException",
                        vm.resolveClass("java/lang/Exception", vm.resolveClass("java/lang/Throwable")))
                        .newObject(new Exception("Package not installed: " + packageName));
                vm.addLocalObject(error);
                vm.throwException(error);
                return null;
            }
            case "java/lang/ClassLoader->getParent()Ljava/lang/ClassLoader;":
                return null;
            case "java/lang/ClassLoader->toString()Ljava/lang/String;":
                return new StringObject(vm, "LightRE.HeadlessClassLoader");
            case "java/lang/Long->toString()Ljava/lang/String;":
                return new StringObject(vm, ((Long) object.getValue()).toString());
            case "java/security/KeyStore->aliases()Ljava/util/Enumeration;":
                try { return vm.resolveClass("java/util/Enumeration")
                        .newObject(((java.security.KeyStore) object.getValue()).aliases()); }
                catch (Exception error) { throw new IllegalStateException(error); }
            case "java/util/Enumeration->nextElement()Ljava/lang/Object;":
                return new StringObject(vm, (String) ((java.util.Enumeration<?>) object.getValue()).nextElement());
            case "java/lang/Class->getClassLoader()Ljava/lang/ClassLoader;":
                if (((DvmClass) object).getClassName().startsWith("java/")) return null;
            case "android/content/Context->getClassLoader()Ljava/lang/ClassLoader;":
                if (applicationLoader == null) applicationLoader = vm.resolveClass("java/lang/ClassLoader").newObject(null);
                return applicationLoader;
            case "android/content/Context->getContentResolver()Landroid/content/ContentResolver;":
                return vm.resolveClass("android/content/ContentResolver").newObject(null);
            case "android/content/Context->getApplicationInfo()Landroid/content/pm/ApplicationInfo;":
                return vm.resolveClass("android/content/pm/ApplicationInfo").newObject(null);
            case "android/content/Context->getCacheDir()Ljava/io/File;":
                return vm.resolveClass("java/io/File").newObject("/data/user/0/com.google.android.gms/cache");
            case "java/io/File->getAbsolutePath()Ljava/lang/String;":
            case "java/io/File->getPath()Ljava/lang/String;":
                return new StringObject(vm, (String) object.getValue());
            case "android/content/Context->getPackageName()Ljava/lang/String;":
                return new StringObject(vm, "com.google.android.gms");
            case "android/content/Context->getApplicationContext()Landroid/content/Context;":
                return object;
            case "android/content/Context->getSystemService(Ljava/lang/String;)Ljava/lang/Object;":
                String service = (String) args.getObjectArg(0).getValue();
                calls.add("system service=" + service);
                if (service.equals("sensor"))
                    return vm.resolveClass("android/hardware/SensorManager").newObject(null);
                if (service.equals("persistent_data_block")) return null;
                if (service.equals("accessibility"))
                    return vm.resolveClass("android/view/accessibility/AccessibilityManager").newObject(null);
                if (service.equals("input"))
                    return vm.resolveClass("android/hardware/input/InputManager").newObject(null);
                if (service.equals("activity"))
                    return vm.resolveClass("android/app/ActivityManager").newObject(null);
                if (service.equals("display"))
                    return vm.resolveClass("android/hardware/display/DisplayManager").newObject(null);
                break;
            case "android/hardware/display/DisplayManager->getDisplays()[Landroid/view/Display;":
                return new ArrayObject();
            case "android/hardware/display/DisplayManager->getDisplay(I)Landroid/view/Display;":
                calls.add("configured headless environment: no display id=" + args.getIntArg(0));
                return null;
            case "android/hardware/SensorManager->getSensorList(I)Ljava/util/List;":
                calls.add("configured environment: no sensors");
                return vm.resolveClass("java/util/ArrayList", vm.resolveClass("java/util/List"))
                        .newObject(new ArrayList<>());
            case "android/hardware/SensorManager->getDefaultSensor(I)Landroid/hardware/Sensor;":
                calls.add("configured environment: no default sensor type=" + args.getIntArg(0));
                return null;
            case "java/util/concurrent/ConcurrentLinkedQueue->poll()Ljava/lang/Object;":
                return (DvmObject<?>) ((Queue<?>) object.getValue()).poll();
            case "java/util/concurrent/ConcurrentHashMap->put(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;":
                return ((Map<Object, DvmObject<?>>) object.getValue())
                        .put(args.getObjectArg(0).getValue(), args.getObjectArg(1));
            case "java/util/concurrent/ConcurrentHashMap->get(Ljava/lang/Object;)Ljava/lang/Object;":
                return (DvmObject<?>) ((Map<?, ?>) object.getValue()).get(args.getObjectArg(0).getValue());
        }
        try {
            return super.callObjectMethodV(vm, object, signature, args);
        } catch (UnsupportedOperationException error) {
            missing.add(signature);
            throw error;
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 3) throw new IllegalArgumentException("SO request.json observation.json");
        Path requestPath = Path.of(args[1]);
        JsonObject request = JsonParser.parseString(Files.readString(requestPath)).getAsJsonObject();
        JsonObject result = new JsonObject();
        DgHarness jni = new DgHarness();
        jni.request = request;
        if (!request.has("environment_profile") || !"headless-v1".equals(request.get("environment_profile").getAsString()))
            throw new IllegalArgumentException("DG requires explicit environment_profile=headless-v1");
        result.addProperty("environment_profile", "headless-v1");
        result.add("jni_calls", jni.calls);
        result.add("missing_jni", jni.missing);
        result.add("opaque_callbacks", jni.callbacks);
        try (AndroidEmulator emulator = AndroidEmulatorBuilder.for64Bit()
                .setProcessName("com.google.android.gms")
                .setRootDir(Path.of("target/rootfs").toFile())
                .addBackendFactory(new Unicorn2Factory(false)).build()) {
            emulator.getMemory().setLibraryResolver(new AndroidResolver(23));
            VM vm = emulator.createDalvikVM();
            vm.setJni(jni);
            vm.setVerbose(true);
            DalvikModule dm = vm.loadLibrary(Path.of(args[0]).toFile(), true);
            dm.callJNI_OnLoad(emulator);
            Module module = dm.getModule();
            DvmClass dg = vm.resolveClass("com/google/ccc/abuse/droidguard/DroidGuard");
            DvmObject<?> instance = dg.newObject(null);
            // unidbg 0.9.9 has no public registry iterator; read its pinned map.
            Field field = DvmClass.class.getDeclaredField("nativesMap");
            field.setAccessible(true);
            @SuppressWarnings("unchecked")
            Map<String, UnidbgPointer> natives = (Map<String, UnidbgPointer>) field.get(dg);
            JsonObject registrations = new JsonObject();
            for (Map.Entry<String, UnidbgPointer> entry : natives.entrySet()) {
                registrations.addProperty(entry.getKey(), "0x" + Long.toHexString(entry.getValue().peer - module.base));
            }
            result.add("registrations", registrations);
            result.addProperty("loaded", true);
            result.addProperty("stage", request.get("stage").getAsString());
            if (!request.get("stage").getAsString().equals("load")) {
                long address = module.base + Long.decode(request.get("vm_entry_rva").getAsString());
                emulator.getBackend().hook_add_new(new CodeHook() {
                    public void hook(Backend backend, long pc, int size, Object user) { jni.vmEntries++; }
                    public void onAttach(UnHook hook) {}
                    public void detach() {}
                }, address, address, null);
                byte[] program = Files.readAllBytes(requestPath.toAbsolutePath().getParent()
                        .resolve(request.get("input_file").getAsString()));
                DvmObject<?> context = vm.resolveClass("android/content/Context").newObject(null);
                DvmObject<?> bundle = vm.resolveClass("android/os/Bundle").newObject(new HashMap<String, Object>());
                long handle = instance.callJniMethodLong(emulator,
                        "initNative(Landroid/content/Context;Ljava/lang/String;[BLjava/lang/Object;Landroid/os/Bundle;II)J",
                        context, new StringObject(vm, "attest"), new ByteArray(vm, program), null, bundle, 0, 0);
                result.addProperty("handle_nonzero", handle > 0);
                result.addProperty("init_vm_entries", jni.vmEntries);
                if (jni.missing.size() > 0) throw new IllegalStateException("Missing JNI: " + jni.missing);
                if (handle <= 0) throw new IllegalStateException("initNative returned an invalid handle: " + handle);
                ArrayObject keys = new ArrayObject(new StringObject(vm, "dg_email"),
                        new StringObject(vm, "dg_package"), new StringObject(vm, "dg_androidId"));
                long beforeSnapshot = jni.vmEntries;
                try {
                    ByteArray output = instance.callJniMethodObject(emulator,
                            "ssNative(J[Ljava/lang/String;Landroid/os/Bundle;)[B", handle, keys, bundle);
                    if (output == null) throw new IllegalStateException("ssNative returned null");
                    result.addProperty("output_hex", hex(output.getValue()));
                    result.addProperty("length", output.getValue().length);
                    Files.write(Path.of(args[2]).toAbsolutePath().getParent().resolve("output.bin"), output.getValue());
                    result.addProperty("ss_vm_entries", jni.vmEntries - beforeSnapshot);
                    result.addProperty("vm_entries", jni.vmEntries);
                } finally {
                    instance.callJniMethod(emulator, "closeNative(J[Ljava/lang/String;)V", handle, keys);
                    result.addProperty("closed", true);
                    result.addProperty("vm_entries", jni.vmEntries);
                }
            }
            Files.writeString(Path.of(args[2]), new GsonBuilder().setPrettyPrinting().create().toJson(result));
        } catch (Throwable error) {
            result.addProperty("vm_entries", jni.vmEntries);
            result.addProperty("error", error.toString());
            Files.writeString(Path.of(args[2]), new GsonBuilder().setPrettyPrinting().create().toJson(result));
            throw error;
        }
    }

    private static String hex(byte[] data) {
        StringBuilder result = new StringBuilder();
        for (byte value : data) result.append(String.format("%02x", value & 255));
        return result.toString();
    }
}
