local total = 0
for i = 1, 20 do
  if i % 2 == 0 then total = total + i else total = total - i end
end
assert(total == 10)
assert(7 * 6 == 42 and 2 ^ 8 == 256)
assert(7 / 2 == 3.5 and 1.25 + 2.5 == 3.75)
assert(bit.band(0x1234, 0xff) == 0x34)
io.write("smoke: 10 42 3.5 3.75\n")
