local t = {}
for i = 1, 4096 do
  t[i] = i * 3
  t["key_" .. i] = i + 7
end
local sum = 0
for i = 1, 4096 do
  assert(t[i] == i * 3 and t["key_" .. i] == i + 7)
  sum = sum + t[i]
end
assert(sum == 25171968)
for i = 1, 4096, 2 do t["key_" .. i] = nil end
for i = 1, 4096, 2 do assert(t["key_" .. i] == nil) end
assert(t["missing"] == nil and t[90000] == nil and rawget(t, nil) == nil)
local obj, fn, proxy = {}, function() end, newproxy(false)
local keys = {true, false, 1.25, -17.5, -1, 0, "", obj, fn, proxy}
for i, key in ipairs(keys) do t[key] = i * 11 end
for i, key in ipairs(keys) do assert(rawget(t, key) == i * 11) end
local parent = {answer = 42}
local child = setmetatable({}, {__index = parent})
assert(child.answer == 42 and child.missing == nil)
local computed = setmetatable({}, {__index = function(_, key) return "fallback:" .. key end})
assert(computed.hello == "fallback:hello")
local sink = {}
local forwarded = setmetatable({}, {__newindex = sink})
forwarded.answer = 17
assert(sink.answer == 17 and rawget(forwarded, "answer") == nil)
io.write("tables: 4096 25171968 keys metatables\n")
