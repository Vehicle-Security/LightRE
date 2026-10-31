#!/usr/bin/env python3
"""Verify real ELF files, strip provenance and selective flattening evidence."""
import argparse
import collections
import json
import os
import re
import struct
import subprocess
import tempfile
from pathlib import Path

from check_size import check_size

ROOT = Path(__file__).resolve().parents[1]


def command(*args):
    return subprocess.check_output(args, text=True, env={**os.environ, "LC_ALL": "C"})


def sections(path):
    """ELF64 little endian section contents, independent of strip's file layout."""
    data = Path(path).read_bytes()
    assert data[:6] == b"\x7fELF\x02\x01", "not ELF64 little endian"
    assert struct.unpack_from("<HH", data, 16) == (3, 183), "not AArch64 ET_DYN"
    table = struct.unpack_from("<Q", data, 40)[0]
    entry_size, count, strings = struct.unpack_from("<HHH", data, 58)
    headers = [struct.unpack_from("<IIQQQQIIQQ", data, table + i * entry_size) for i in range(count)]
    string_header = headers[strings]
    names = data[string_header[4]:string_header[4] + string_header[5]]
    result = {}
    for name, kind, flags, address, offset, size, *_ in headers:
        text = names[name:names.index(b"\0", name)].decode()
        result[text] = {"type": kind, "flags": flags, "address": address, "size": size,
                        "bytes": b"" if kind == 8 else data[offset:offset + size]}
    return result


def verify_elf():
    size = check_size("deliverable")
    assert set(size["files"]) == {"minilua", "smoke.lua"}
    details = {}
    for full, stripped in [("reference/baseline.full", "reference/baseline.stripped"),
                           ("reference/obfuscated.full", "deliverable/minilua")]:
        before, after = sections(full), sections(stripped)
        assert ".symtab" in before and ".symtab" not in after
        assert not any(n.startswith((".debug", ".zdebug")) for n in after)
        loaded = lambda items: {n: v for n, v in items.items() if v["flags"] & 2}
        assert loaded(before) == loaded(after), "strip altered allocated sections"
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "stripped"
            subprocess.run(["aarch64-linux-gnu-strip", "--strip-all", "-o", copy, full], check=True)
            assert copy.read_bytes() == Path(stripped).read_bytes(), "strip provenance mismatch"
        headers = command("aarch64-linux-gnu-readelf", "-WhlSdV", stripped)
        assert "INTERP" in headers and "DYNAMIC" in headers
        assert re.search(r"FLAGS_1.*\bPIE\b", headers), "missing dynamic PIE flag"
        needed = re.findall(r"Shared library: \[(.*?)\]", headers)
        assert "libc.so.6" in needed and "libm.so.6" in needed
        interpreter = re.search(r"Requesting program interpreter: (.*?)\]", headers).group(1)
        glibc_versions = sorted(set(re.findall(r"\bGLIBC_\d+\.\d+\b", headers)),
                                key=lambda v: tuple(map(int, v.removeprefix("GLIBC_").split("."))))
        Path(f"reports/{Path(stripped).name}.readelf.txt").write_text(headers)
        details[stripped] = {"architecture": "AArch64", "class": "ELF64", "endian": "little",
                             "type": "DYN / PIE", "dynamic": True, "interpreter": interpreter,
                             "needed": needed, "symtab": False, "dwarf": False,
                             "glibc_symbol_versions": glibc_versions,
                             "strip_reproduces_exact_bytes": True, "allocated_sections_identical": True}
    return {"passed": True, "files": details, "size": size}


def functions(ir):
    pattern = r"^define [^\n]*@([^ (]+)\([^\n]*\)[^\n]*\{\n.*?^}"
    return {m.group(1): m.group(0) for m in re.finditer(pattern, ir, re.M | re.S)}


def canonical_functions(ir):
    # Bitcode serialization renumbers metadata IDs when blocks move. Resolve
    # their contents instead of dropping optimization/alias metadata.
    metadata = dict(re.findall(r"^!(\d+) = ([^\n]+)$", ir, re.M))
    attributes = dict(re.findall(r"^attributes #(\d+) = ([^\n]+)$", ir, re.M))

    def expand(key, ancestors=()):
        if key in ancestors:
            return "!<cycle>"
        return re.sub(r"!(\d+)", lambda m: expand(m[1], (*ancestors, key)), metadata[key])

    def normalize(body):
        body = re.sub(r"!(\d+)", lambda m: expand(m[1]), body)
        return re.sub(r"#(\d+)\b", lambda m: attributes[m[1]], body)

    return {name: normalize(body) for name, body in functions(ir).items()}


def assembly_function(assembly, name):
    found = re.search(rf"^[0-9a-f]+ <{re.escape(name)}>:\n.*?(?=\n[0-9a-f]+ <|\Z)",
                      assembly, re.M | re.S)
    assert found, f"missing function in machine code: {name}"
    return found.group(0).rstrip() + "\n"


def verify_flattening(*, large=False):
    targets = json.loads(Path("sources.json").read_text())["targets"]
    modules = {v: Path(f"reference/{v}.ll").read_text() for v in ["baseline", "obfuscated"]}
    ir = {v: functions(text) for v, text in modules.items()}
    canonical = {v: canonical_functions(text) for v, text in modules.items()}
    assert ir["baseline"].keys() == ir["obfuscated"].keys(), "function set changed"
    changed = sorted(n for n in canonical["baseline"] if canonical["baseline"][n] != canonical["obfuscated"][n])
    assert changed == sorted(targets), f"unexpected modified functions: {changed}"
    vm_location = "luaV_execute" if "luaV_execute" in ir["baseline"] else "luaD_call"
    assert vm_location in ir["baseline"]
    assert (vm_location in changed) == large
    result = {"passed": True, "changed_functions": changed,
              "unchanged_function_count": len(ir["baseline"]) - len(changed),
              "ir_comparison": "resolve metadata/attribute ID references to contents; preserve instructions",
              "vm_ir_location": vm_location, "vm_container_ir_unchanged": not large, "targets": {}}
    for variant in ["baseline", "obfuscated"]:
        assembly = Path(f"reference/{variant}.asm").read_text()
        symbols = command("aarch64-linux-gnu-nm", "-S", "--defined-only", f"reference/{variant}.full")
        for name in targets:
            body = ir[variant][name]
            text = assembly_function(assembly, name)
            Path(f"reference/{variant}.{name}.ll").write_text(body + "\n")
            Path(f"reference/{variant}.{name}.asm").write_text(text)
            symbol = re.search(rf"^([0-9a-f]+) ([0-9a-f]+) [tT] {name}$", symbols, re.M)
            assert symbol, f"missing exact non-inlined symbol: {name}"
            instructions = re.findall(r"^[ \t]*([0-9a-f]+):[ \t]+[0-9a-f]{8}[ \t]+(\S+)[ \t]*([^\n]*)", text, re.M)
            # AArch64 instructions are four bytes; exclude inter-function padding.
            start, size = int(symbol[1], 16), int(symbol[2], 16)
            instructions = [i for i in instructions if start <= int(i[0], 16) < start + size]
            assert [int(i[0], 16) for i in instructions] == list(range(start, start + size, 4))
            destinations = collections.Counter()
            for _, opcode, operands in instructions:
                if opcode == "b":
                    destination = operands.split()[0]
                    destinations[destination] += 1
            metrics = {"address": f"0x{symbol.group(1)}", "code_bytes": int(symbol.group(2), 16),
                       "ir_blocks": 1 + len(re.findall(r"^[\w.$-]+:", body, re.M)),
                       "native_blocks": "未测量", "instruction_count": len(instructions),
                       "indirect_jumps_br": sum(op == "br" for _, op, _ in instructions),
                       "indirect_calls_blr": sum(op == "blr" for _, op, _ in instructions),
                       "unconditional_branch_targets": dict(destinations.most_common()),
                       "state_stores_ir": len(re.findall(r"store i32 [^\n]*i32\* %switchVar\w*,", body))}
            if large and variant == "obfuscated":
                minimum = json.loads(Path("sources.json").read_text())["large"]["minimum_instructions"]
                assert len(instructions) >= minimum, "large function is below the requested complexity"
            if variant == "obfuscated":
                assert "%switchVar = alloca i32" in body
                assert "loopEntry:" in body and "loopEnd:" in body
                assert re.search(r"switch i32 %switchVar\w*, label %switchDefault", body)
                assert metrics["state_stores_ir"] >= 3
                assert len(re.findall(r"br label %loopEnd", body)) >= 2
                # Backend must retain repeated returns to a shared dispatch region.
                assert destinations and destinations.most_common(1)[0][1] >= 3
                common = destinations.most_common(1)[0][0]
                index = next(i for i, instruction in enumerate(instructions) if instruction[0] == common)
                dispatch = instructions[index:index + 5]
                assert dispatch[0][1] in ["ldr", "ldur"] and dispatch[0][2].startswith("w")
                assert any(op == "cmp" for _, op, _ in dispatch)
                metrics["dispatcher_excerpt"] = [f"0x{addr}: {op} {operands}" for addr, op, operands in dispatch]
            result["targets"].setdefault(name, {})[variant] = metrics
    return result


def report(elf, cfg, tests):
    lines = ["# minilua 实测验收报告", "", "构建、测试、ELF 与 IR 检查均通过。",
             "", f"交付目录：**{elf['size']['total_bytes']} / 300000 字节**。",
             f"差分测试：{tests['case_count']} 组，{tests['execution_count']} 次实际执行；每次超时上限 {tests['timeout_seconds']} 秒。",
             "基线 full/stripped 与混淆 full/stripped 的 stdout、stderr、退出码逐字节一致，没有输出归一化。",
             "", "## ELF 与参考文件", "",
             "两版都是 AArch64、ELF64、小端、动态 PIE。stripped 文件没有 .symtab 或 DWARF。",
             "重新 strip 每个 full 文件，所得字节与对应 stripped 文件完全相同；所有 SHF_ALLOC section 内容与地址一致。",
             "因此参考文件与交付文件来自同一次链接，未重新编译参考程序。", ""]
    target_elf = elf["files"]["deliverable/minilua"]
    lines += [f"解释器：`{target_elf['interpreter']}`；动态依赖：`{', '.join(target_elf['needed'])}`。",
              f"引用的 glibc 符号版本：`{', '.join(target_elf['glibc_symbol_versions'])}`。",
              "完整 ELF 检查输出见 `reports/*.readelf.txt`。", "", "## 混淆范围与执行阶段", "",
              "固定 eshard/obfuscator-llvm + 函数白名单补丁；白名单在 LowerSwitch 之前检查。",
              "Clang 完成 -O2（或显式选择的 -Os）IR 优化后，显式运行 `opt -passes=function(flattening),verify`，随后 llc -O2 生成 PIC 目标文件。",
              "没有运行 bogus、substitution、split-basic-blocks、string-encryption；没有反调试或加壳。",
              "两个版本使用同一份仅添加两处 noinline 的源码、相同编译器与链接参数；没有 LTO 或平坦化后的 IR 优化。",
              f"逐函数比较 IR：解析 metadata/attribute 编号为内容后，恰好只有 `{', '.join(cfg['changed_functions'])}` 改变，另外 {cfg['unchanged_function_count']} 个函数一致。",
              f"VM 主循环所在函数 `{cfg['vm_ir_location']}` 的 IR 一致；未对其添加混淆或额外 noinline。",
              "当 vm_ir_location 为 luaD_call 时，说明 Clang 已将 luaV_execute 内联进调用者。",
              "", "## 两个目标函数的证据", "",
              "|函数|版本|RVA|代码字节|IR 基本块|机器指令|br 间接跳转|",
              "|---|---|---|---:|---:|---:|---:|"]
    for name, versions in cfg["targets"].items():
        for variant, m in versions.items():
            lines.append(f"|{name}|{variant}|{m['address']}|{m['code_bytes']}|{m['ir_blocks']}|{m['instruction_count']}|{m['indirect_jumps_br']}|")
    lines += ["", "IR 基本块包含入口块；机器码基本块数量未测量，未将反汇编行数当作 CFG 计数。",
              "平坦化可被后端降为比较树，因此不要求出现 br 间接跳转。", ""]
    for name, versions in cfg["targets"].items():
        m = versions["obfuscated"]
        common, count = next(iter(m["unconditional_branch_targets"].items()))
        lines += [f"### {name}", "",
                  f"对照：`reference/baseline.{name}.ll/.asm` 与 `reference/obfuscated.{name}.ll/.asm`。",
                  f"混淆 IR 有 `%switchVar` 状态槽、`loopEntry` 中央 switch、{m['state_stores_ir']} 处状态写入；",
                  "原基本块的条件边改为 select 选择状态后 store，或直接写下一状态，然后跳到 loopEnd → loopEntry。return 块仍可直接返回。",
                  f"实际机器码有 {count} 条无条件分支回到共享地址 `0x{common}`，配合反汇编中的状态加载和常量比较，验证分派未被后端消除。",
                  "分派入口实测片段：", "", "```asm", *m["dispatcher_excerpt"], "```", "",
                  "带地址的完整目标函数反汇编保存在上述 .asm 文件，可从状态写入一路追踪到分派比较树。", ""]
    lines += ["## 功能与能力边界", "",
              "覆盖整数/浮点与分支、数字/字符串/对象键、扩容/删除/缺失查询、闭包修改、递归、可变参数、",
              "__index（表/函数）与 __newindex、pcall、nil/NaN 键错误、未捕获错误、语法错误、CLI 参数及无脚本退出码。",
              "allocation.lua 分配 60000 组临时对象，并断言弱引用目标在进程结束前消失，证明自动 GC 实际发生。",
              "上游 minilua 缺少 print/math/collectgarbage；capabilities.lua 实测确认，使用 io.write、原生算术、弱引用替代。",
              "所有成功案例检查独立的预期输出，错误案例检查非零退出码与明确诊断，再进行四版本差分。", "",
              "运行方式、sysroot、实际命令、原始输出见 `reports/runtime.json`、`reports/tests.json`。",
              "固定源码、工具版本、完整构建参数和文件 SHA-256 见 `sources.json`、`manifest.json`。", ""]
    Path("reports/verification.md").write_text("\n".join(lines))


def large_report(elf, cfg, tests):
    versions = cfg["targets"]["luaV_execute"]
    obf = versions["obfuscated"]
    lines = ["# minilua 大函数实测报告", "",
             f"交付目录总计 {elf['size']['total_bytes']} / 300000 字节。",
             f"功能差分验证：{tests['case_count']} 组 / {tests['execution_count']} 次实际运行通过；",
             "基线 full/stripped、混淆 full/stripped 的输出、错误和退出码一致。", "",
             "仅对真实解释器 luaV_execute 拆分基本块，再平坦化两轮；未填充无意义指令或复制解释器。",
             "两个版本使用相同源码（额外给 VM 添加 noinline）、Clang -Os、llc -O2、动态 PIE 链接参数。",
             "链接最大页尺寸为 16384 字节；没有 LTO 或混淆后的 IR 优化。",
             f"逐函数解析 metadata/attributes 后比较：仅 luaV_execute 的 IR 改变，{cfg['unchanged_function_count']} 个函数一致。", "",
             "|版本|RVA|代码字节|机器指令|IR 基本块|", "|---|---|---:|---:|---:|"]
    for name, m in versions.items():
        lines.append(f"|{name}|{m['address']}|{m['code_bytes']}|{m['instruction_count']}|{m['ir_blocks']}|")
    common, count = next(iter(obf["unconditional_branch_targets"].items()))
    lines += ["", "机器指令按 ELF 符号范围逐地址核对：连续的四字节 AArch64 指令，没有重复地址；",
              "代码字节数等于指令数乘四。机器 CFG 基本块数未测量。",
              f"混淆 IR 有 {obf['state_stores_ir']} 处状态写入，机器码有 {count} 条无条件分支回到 0x{common}。",
              "共享分派入口实测：", "", "```asm", *obf["dispatcher_excerpt"], "```", "",
              "完整参考：reference/{baseline,obfuscated}.luaV_execute.{ll,asm}。",
              "ELF、动态依赖、strip 同源检查和大小结果见 reports/elf.json；",
              "实际测试命令、原始输出、耗时见 reports/tests.json；固定工具与参数见 manifest.json。", "",
              "这提供与真实大函数相近的规模和多层分派压力，不代表复现未知商业保护的全部机制。", ""]
    Path("reports/verification.md").write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--large", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT / "large" if args.large else ROOT)
    for name in ["verification.md", "elf.json", "cfg.json"]:
        Path(f"reports/{name}").unlink(missing_ok=True)
    tests = json.loads(Path("reports/tests.json").read_text())
    assert tests["passed"]
    elf = verify_elf()
    cfg = verify_flattening(large=args.large)
    Path("reports/elf.json").write_text(json.dumps(elf, indent=2, ensure_ascii=False) + "\n")
    Path("reports/cfg.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    if args.large:
        large_report(elf, cfg, tests)
    else:
        report(elf, cfg, tests)
    print(f"PASS ELF/strip provenance/selective flattening/size: {elf['size']['total_bytes']} bytes")


if __name__ == "__main__":
    main()
