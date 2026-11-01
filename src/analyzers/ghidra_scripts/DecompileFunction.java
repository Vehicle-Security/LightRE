// Read-only targeted decompilation with explicit, evidence-backed switch targets.
// @category LightRE
import ghidra.app.util.headless.HeadlessScript;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.listing.Function;
import ghidra.program.model.pcode.JumpTable;
import ghidra.program.model.symbol.RefType;
import ghidra.program.model.symbol.SourceType;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;

public class DecompileFunction extends HeadlessScript {
    private Address address(long rva) {
        return currentProgram.getImageBase().add(rva);
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        JsonObject plan = JsonParser.parseString(Files.readString(Path.of(args[0]))).getAsJsonObject();
        if (!plan.get("sha256").getAsString().equals(currentProgram.getExecutableSHA256())) {
            throw new IllegalArgumentException("Saved Ghidra project belongs to another binary");
        }
        long rva = plan.get("rva").getAsLong();
        Function function = getFunctionAt(address(rva));
        if (function == null) throw new IllegalArgumentException("Unknown function RVA: " + rva);
        JsonObject output = new JsonObject();
        output.addProperty("rva", rva);
        output.addProperty("image_base", currentProgram.getImageBase().getOffset());
        output.addProperty("original_body_bytes", function.getBody().getNumAddresses());
        for (var element : plan.getAsJsonArray("jumps")) {
            var jump = element.getAsJsonObject();
            Address branch = address(jump.get("rva").getAsLong());
            disassemble(branch);
            var instruction = getInstructionAt(branch);
            if (instruction == null || !instruction.getFlowType().isJump()
                    || !instruction.getFlowType().isComputed()) {
                throw new IllegalArgumentException("Not a computed jump: " + branch);
            }
            var branchOwner = getFunctionContaining(branch);
            if (branchOwner != null && !branchOwner.equals(function)) {
                throw new IllegalArgumentException("Jump belongs to another function: " + branch);
            }
            // Nested switches may themselves lie in a previously missed block.
            // Register just that instruction before writing its override; the
            // final fixup derives the full body from reachable control flow.
            if (!function.getBody().contains(branch)) {
                AddressSet body = new AddressSet(function.getBody());
                body.addRange(branch, instruction.getMaxAddress());
                function.setBody(body);
            }
            ArrayList<Address> targets = new ArrayList<>();
            for (var value : jump.getAsJsonArray("targets")) {
                Address target = address(value.getAsLong());
                var owner = getFunctionContaining(target);
                if (owner != null && !owner.equals(function)) {
                    throw new IllegalArgumentException("Target belongs to another function: " + target);
                }
                disassemble(target);
                if (getInstructionAt(target) == null) {
                    throw new IllegalArgumentException("Cannot decode target: " + target);
                }
                instruction.addOperandReference(0, target, RefType.COMPUTED_JUMP, SourceType.USER_DEFINED);
                targets.add(target);
            }
            new JumpTable(branch, targets, true, 0).writeOverride(function);
        }
        if (!plan.getAsJsonArray("jumps").isEmpty()) {
            CreateFunctionCmd.fixupFunctionBody(currentProgram, function, monitor);
        }
        output.addProperty("body_bytes", function.getBody().getNumAddresses());
        output.add("jumps", plan.getAsJsonArray("jumps"));
        output.addProperty("style", plan.get("style").getAsString());
        DecompInterface decompiler = new DecompInterface();
        try {
            DecompileOptions options = new DecompileOptions();
            options.grabFromProgram(currentProgram);
            options.setMaxInstructions(Math.max(options.getMaxInstructions(), 200000));
            decompiler.setOptions(options);
            decompiler.setSimplificationStyle(plan.get("style").getAsString());
            if (!decompiler.openProgram(currentProgram)) throw new IllegalStateException(decompiler.getLastMessage());
            var result = decompiler.decompileFunction(function, plan.get("timeout").getAsInt(), monitor);
            output.addProperty("completed", result.decompileCompleted());
            output.addProperty("error", result.getErrorMessage());
            output.addProperty("code", result.getDecompiledFunction() == null ? null : result.getDecompiledFunction().getC());
        }
        finally { decompiler.dispose(); }
        Files.writeString(Path.of(args[1]), new GsonBuilder().serializeNulls().setPrettyPrinting().create().toJson(output));
    }
}
