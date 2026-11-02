package lightre;

import com.github.unidbg.AndroidEmulator;
import com.github.unidbg.Module;
import com.github.unidbg.Symbol;
import com.github.unidbg.arm.backend.Unicorn2Factory;
import com.github.unidbg.linux.android.AndroidEmulatorBuilder;
import com.github.unidbg.linux.android.AndroidResolver;
import com.github.unidbg.memory.MemoryBlock;
import com.google.gson.Gson;
import com.google.gson.JsonObject;

import java.nio.file.Files;
import java.nio.file.Path;

/** Known signatures in native_smoke.c; each invocation gets a fresh emulator. */
public final class NativeHarness {
    private static Symbol required(Module module, String name) {
        Symbol symbol = module.findSymbolByName(name);
        if (symbol == null) throw new IllegalArgumentException("Missing export: " + name);
        return symbol;
    }

    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder();
        for (byte value : bytes) result.append(String.format("%02x", value & 255));
        return result.toString();
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 3) throw new IllegalArgumentException("SO request.json observation.json");
        Path requestPath = Path.of(args[1]);
        JsonObject request = new Gson().fromJson(Files.readString(requestPath), JsonObject.class);
        byte[] input = Files.readAllBytes(requestPath.getParent().resolve(request.get("input_file").getAsString()));
        int capacity = request.get("capacity").getAsInt();
        try (AndroidEmulator emulator = AndroidEmulatorBuilder.for64Bit()
                .setProcessName("org.lightre.smoke")
                .setRootDir(Path.of("target", "rootfs").toFile())
                .addBackendFactory(new Unicorn2Factory(false)).build()) {
            emulator.getMemory().setLibraryResolver(new AndroidResolver(23));
            Module module = emulator.loadLibrary(Path.of(args[0]).toFile(), false);
            Symbol arithmetic = required(module, "smoke_arithmetic");
            Symbol transform = required(module, "smoke_transform");
            long integer = arithmetic.call(emulator, request.get("a").getAsInt(), request.get("b").getAsInt()).longValue();
            MemoryBlock in = emulator.getMemory().malloc(Math.max(1, input.length), true);
            MemoryBlock out = emulator.getMemory().malloc(Math.max(1, capacity), true);
            try {
                in.getPointer().write(0, input, 0, input.length);
                out.getPointer().setMemory(0, Math.max(1, capacity), (byte)0x5a);
                int length = transform.call(emulator, in.getPointer(), input.length,
                        out.getPointer(), capacity, request.get("key").getAsInt()).intValue();
                if (length < -1 || length > capacity) throw new IllegalStateException("Invalid output length: " + length);
                JsonObject result = new JsonObject();
                result.addProperty("integer", integer);
                result.addProperty("length", length);
                result.addProperty("output_hex", hex(out.getPointer().getByteArray(0, Math.max(0, length))));
                result.addProperty("backend", "unicorn2");
                result.addProperty("unidbg", "0.9.9");
                Files.writeString(Path.of(args[2]), new Gson().toJson(result));
            } finally {
                out.free();
                in.free();
            }
        }
    }
}
