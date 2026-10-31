local function counter(start)
  local value = start
  return function(delta) value = value + delta; return value end,
         function() return value end
end
local add, read = counter(10)
assert(add(3) == 13 and add(-5) == 8 and read() == 8)
local function factorial(n)
  if n < 2 then return 1 end
  return n * factorial(n - 1)
end
assert(factorial(10) == 3628800)
local function sum(...)
  local args, result = {...}, 0
  for i = 1, #args do result = result + args[i] end
  return result, #args
end
local result, count = sum(2, 3, 5, 7)
assert(result == 17 and count == 4)
local args = {...}
assert(args[1] == "fixed-argument" and arg[0] == "tests/closures.lua")
io.write("closures: 8 recursion: 3628800 varargs: 17 4\n")
