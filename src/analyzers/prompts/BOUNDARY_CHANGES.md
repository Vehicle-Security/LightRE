

### Ghidra 边界调整
{% for item in CHANGES %}- 原条目 `{{ '%#x' | format(item.old_rva) }}`：{{item.kind}}。旧代码与评价保存在 `{{item.archive}}`；旧知识仍可用 read_knowledge(reader, {{ '%#x' | format(item.old_rva) }}) 读取。
{% endfor %}
