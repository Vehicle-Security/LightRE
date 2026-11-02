package lightre;

import com.github.unidbg.AndroidEmulator;
import com.github.unidbg.Module;
import com.github.unidbg.Symbol;
import com.github.unidbg.arm.backend.*;
import com.github.unidbg.linux.android.AndroidEmulatorBuilder;
import com.github.unidbg.linux.android.AndroidResolver;
import com.github.unidbg.memory.MemoryBlock;
import com.github.unidbg.pointer.UnidbgPointer;
import com.google.gson.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;

/** Pure C ABI: no Dalvik VM, JNI or JNI_OnLoad. */
public final class MiniluaHarness {
    private static Symbol required(Module module, String name) {
        Symbol symbol = module.findSymbolByName(name);
        if (symbol == null) throw new IllegalArgumentException("Missing export: " + name);
        return symbol;
    }

    private static MemoryBlock bytes(AndroidEmulator emulator, byte[] data) {
        MemoryBlock block = emulator.getMemory().malloc(data.length + 1, true);
        block.getPointer().write(0, data, 0, data.length);
        block.getPointer().setByte(data.length, (byte) 0);
        return block;
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 3) throw new IllegalArgumentException("SO request.json observation.json");
        Path requestPath = Path.of(args[1]).toAbsolutePath();
        Path output = Path.of(args[2]).toAbsolutePath();
        JsonObject request = JsonParser.parseString(Files.readString(requestPath)).getAsJsonObject();
        byte[] source = Files.readAllBytes(requestPath.getParent().resolve(request.get("input_file").getAsString()));
        try (AndroidEmulator emulator = AndroidEmulatorBuilder.for64Bit()
                .setProcessName("org.lightre.minilua")
                .setRootDir(Path.of("target/rootfs").toFile())
                .addBackendFactory(new Unicorn2Factory(false)).build()) {
            emulator.getMemory().setLibraryResolver(new AndroidResolver(23));
            Module module = emulator.loadLibrary(Path.of(args[0]).toFile(), true);
            Symbol run = required(module, "lightre_minilua_run");
            Symbol text = required(module, "lightre_minilua_result");
            Symbol length = required(module, "lightre_minilua_length");
            long vmEntry = required(module, "lightre_minilua_vm_entry").call(emulator).longValue();
            long[] entries = {0};
            emulator.getBackend().hook_add_new(new CodeHook() {
                public void hook(Backend backend, long pc, int size, Object user) { entries[0]++; }
                public void onAttach(UnHook hook) {}
                public void detach() {}
            }, vmEntry, vmEntry, null);
            MemoryBlock input = bytes(emulator, source);
            MemoryBlock chunk = bytes(emulator, request.get("chunk_name").getAsString().getBytes(StandardCharsets.UTF_8));
            MemoryBlock argument = request.has("argument")
                    ? bytes(emulator, request.get("argument").getAsString().getBytes(StandardCharsets.UTF_8)) : null;
            try {
                int status = run.call(emulator, input.getPointer(), source.length, chunk.getPointer(),
                        argument == null ? null : argument.getPointer()).intValue();
                if (status < 0) throw new IllegalStateException("minilua ABI error: " + status);
                JsonObject result = new JsonObject();
                result.addProperty("lua_status", status);
                result.addProperty("exit_code", status == 0 ? 0 : 1);
                result.addProperty("vm_entries", entries[0]);
                result.addProperty("vm_entry_rva", "0x" + Long.toHexString(vmEntry - module.base));
                for (int i = 0; i < 3; ++i) {
                    int size = length.call(emulator, i).intValue();
                    if (size < 0 || size >= 1024 * 1024) throw new IllegalStateException("Invalid result length");
                    long pointer = text.call(emulator, i).longValue();
                    byte[] data = UnidbgPointer.pointer(emulator, pointer).getByteArray(0, size);
                    if (i == 2) {
                        Files.write(output.getParent().resolve("proto.txt"), data);
                        result.addProperty("proto_file", "proto.txt");
                        result.addProperty("proto_bytes", data.length);
                    } else {
                        result.addProperty(i == 0 ? "stdout" : "stderr", new String(data, StandardCharsets.UTF_8));
                    }
                }
                Files.writeString(output, new GsonBuilder().setPrettyPrinting().create().toJson(result));
            } finally {
                input.free(); chunk.free(); if (argument != null) argument.free();
            }
        }
    }
}
