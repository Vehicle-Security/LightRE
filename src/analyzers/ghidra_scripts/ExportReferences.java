// Export one authoritative Ghidra catalog, assembly, and reference pseudocode.
// @category LightRE
import ghidra.app.util.headless.HeadlessScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.SourceType;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.io.BufferedWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;

public class ExportReferences extends HeadlessScript {
    private String hex(byte[] bytes) {
        char[] digits = "0123456789abcdef".toCharArray();
        char[] result = new char[bytes.length * 2];
        for (int i = 0; i < bytes.length; i++) {
            result[2 * i] = digits[(bytes[i] & 255) >>> 4];
            result[2 * i + 1] = digits[bytes[i] & 15];
        }
        return new String(result);
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        Gson gson = new GsonBuilder().serializeNulls().create();
        long base = currentProgram.getImageBase().getOffset();
        JsonObject root = new JsonObject();
        root.addProperty("sha256", args[2]);
        root.addProperty("image_base", base);
        root.addProperty("export_schema", 1);
        root.addProperty("language", currentProgram.getLanguageID().toString());
        root.addProperty("analysis_timed_out", analysisTimeoutOccurred());
        JsonArray sections = new JsonArray();
        for (var block : currentProgram.getMemory().getBlocks()) {
            if (!block.getStart().isMemoryAddress()) continue;
            JsonObject section = new JsonObject();
            section.addProperty("name", block.getName());
            section.addProperty("start", block.getStart().getOffset() - base);
            section.addProperty("end", block.getEnd().getOffset() - base + 1);
            section.addProperty("executable", block.isExecute());
            sections.add(section);
        }
        root.add("sections", sections);
        ArrayList<Function> selected = new ArrayList<>();
        var iterator = currentProgram.getFunctionManager().getFunctions(true);
        while (iterator.hasNext()) {
            Function function = iterator.next();
            if (function.isExternal() || !function.getEntryPoint().isMemoryAddress()) continue;
            long rva = function.getEntryPoint().getOffset() - base;
            if (function.getName().startsWith("FUN_")) {
                function.setName(String.format("fcn_%08x", rva), SourceType.ANALYSIS);
            }
            selected.add(function);
        }
        JsonArray functions = new JsonArray();
        DecompInterface decompiler = new DecompInterface();
        try (BufferedWriter assembly = Files.newBufferedWriter(Path.of(args[1]), StandardCharsets.UTF_8)) {
            if (!decompiler.openProgram(currentProgram)) {
                throw new IllegalStateException(decompiler.getLastMessage());
            }
            for (Function function : selected) {
                monitor.checkCancelled();
                long rva = function.getEntryPoint().getOffset() - base;
                JsonArray instructions = new JsonArray();
                JsonArray ranges = new JsonArray();
                long start = -1, end = -1;
                var listing = currentProgram.getListing().getInstructions(function.getBody(), true);
                while (listing.hasNext()) {
                    var instruction = listing.next();
                    long address = instruction.getAddress().getOffset() - base;
                    if (start < 0 || address != end) {
                        if (start >= 0) {
                            JsonArray range = new JsonArray(); range.add(start); range.add(end); ranges.add(range);
                        }
                        start = address;
                    }
                    end = address + instruction.getLength();
                    JsonObject op = new JsonObject();
                    op.addProperty("rva", address);
                    op.addProperty("bytes", hex(instruction.getBytes()));
                    op.addProperty("opcode", instruction.toString());
                    instructions.add(op);
                }
                if (start >= 0) {
                    JsonArray range = new JsonArray(); range.add(start); range.add(end); ranges.add(range);
                }
                if (instructions.size() == 0) continue;
                JsonObject row = new JsonObject();
                row.addProperty("rva", rva);
                row.add("instructions", instructions);
                assembly.write(gson.toJson(row));
                assembly.newLine();

                JsonObject item = new JsonObject();
                item.addProperty("rva", rva);
                item.addProperty("name", function.getName());
                item.addProperty("instructions", instructions.size());
                item.add("ranges", ranges);
                item.addProperty("body_bytes", function.getBody().getNumAddresses());
                var block = currentProgram.getMemory().getBlock(function.getEntryPoint());
                String module = block == null ? "recovered" : block.getName();
                String moduleSource = block == null ? "fallback" : "memory_block";
                if (function.isThunk() || module.contains("plt")) {
                    module = "imports"; moduleSource = "import_or_thunk";
                }
                else if (!function.getParentNamespace().isGlobal()) {
                    module = function.getParentNamespace().getName(true); moduleSource = "namespace";
                }
                item.addProperty("module", module);
                item.addProperty("module_source", moduleSource);
                item.addProperty("code", (String)null);
                item.addProperty("status", "error");
                try {
                    DecompileResults result = decompiler.decompileFunction(function, Integer.parseInt(args[3]), monitor);
                    if (result.decompileCompleted() && result.getDecompiledFunction() != null) {
                        item.addProperty("status", "ok");
                        item.addProperty("code", result.getDecompiledFunction().getC());
                        item.addProperty("error", "");
                    }
                    else item.addProperty("error", result.getErrorMessage());
                }
                catch (Exception error) {
                    item.addProperty("error", error.toString());
                }
                functions.add(item);
            }
        }
        finally { decompiler.dispose(); }
        root.add("functions", functions);
        Files.writeString(Path.of(args[0]), gson.toJson(root), StandardCharsets.UTF_8);
        println("LightRE exported " + functions.size() + " Ghidra functions with assembly");
    }
}
