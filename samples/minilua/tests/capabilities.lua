-- This is LuaJIT's build interpreter, not the LuaJIT runtime or full Lua 5.1.
assert(_VERSION == "Lua 5.1")
assert(print == nil and math == nil and collectgarbage == nil)
assert(type(io.write) == "function" and type(bit.band) == "function")
io.write("capabilities: Lua 5.1 subset; io.write bit automatic-GC\n")
