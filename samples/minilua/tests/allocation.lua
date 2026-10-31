-- Weak values make automatic collection observable without collectgarbage().
local weak = setmetatable({}, {__mode = "v"})
do
  local object = {"collect me"}
  weak[1] = object
end
local checksum = 0
for i = 1, 60000 do
  local temporary = {i, string.rep("x", 80) .. i, child = {i + 1}}
  checksum = checksum + temporary.child[1]
end
assert(checksum == 1800090000)
assert(weak[1] == nil, "automatic GC did not reclaim the weak value")
io.write("allocation: 60000 checksum: 1800090000 automatic-GC: observed\n")
