"""Template context is opaque data, including C, JSON and template markers."""

import tempfile
import unittest
from pathlib import Path

from lightre.prompt import render_prompt
from lightre.paths import PACKAGE_DIR
from rehost.guide import ensure, record, BEGIN, END
import recovery


class PromptTests(unittest.TestCase):
    def test_inserted_data_is_not_rendered_again(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'prompt.md'
            path.write_text('任务：{{TASK}}\n上下文：{{CONTEXT}}\n', encoding='utf-8')
            context = '{"code": "return {{TASK}};", "shell": "${HOME}"}'
            result = render_prompt(path, TASK='还原函数', CONTEXT=context)
        self.assertEqual(result, f'任务：还原函数\n上下文：{context}\n')

    def test_missing_context_reports_the_template_and_variable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'prompt.md'
            path.write_text('任务：{{TASK}}，输入：{{INPUT}}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, r"prompt\.md: 模板渲染失败：'INPUT' is undefined"):
                render_prompt(path, TASK='还原函数')

    def test_template_controls_defaults_branches_and_lists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'prompt.md'
            path.write_text("{{ TASK or '默认任务' }}\n{% if ITEMS %}{% for item in ITEMS %}- {{ item | json }}\n{% endfor %}{% else %}无输入\n{% endif %}", encoding='utf-8')
            self.assertEqual(render_prompt(path, TASK='', ITEMS=['中文', '<C & JSON>']),
                             '默认任务\n- "中文"\n- "<C & JSON>"\n')
            self.assertEqual(render_prompt(path, TASK='自定义任务', ITEMS=[]), '自定义任务\n无输入\n')

    def test_template_syntax_error_identifies_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'broken.md'
            path.write_text('{% if TASK %}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, r'broken\.md: 模板渲染失败'):
                render_prompt(path, TASK='还原函数')

    def test_packaged_exploration_template_keeps_candidate_paths_literal(self):
        path = PACKAGE_DIR / 'prompts/EXPLORE.md'
        result = render_prompt(path, DIRECTORY='/inputs/{{TASK}}',
                               CANDIDATES=['主库.so', '依赖/{{UNKNOWN}}.so'])
        self.assertIn('输入目录：/inputs/{{TASK}}', result)
        self.assertIn('- 主库.so\n- 依赖/{{UNKNOWN}}.so', result)

    def test_recovery_templates_keep_code_and_assembly_inputs_separate(self):
        prompts = Path(recovery.__file__).parent / 'prompts'
        code = 'int test(void) { return {{TASK}}; }'
        context = dict(FUNCTIONS=[{'rva': '0x4', 'instructions': 1, 'assembly_text': '4 ret',
                                   'snapshot': {'ranges': [[4, 8]]}, 'knowledge': '已有线索'}],
                       CONTEXT=code, COMMENT='修改返回值', PARENT_TASK=None,
                       SKILL={'name': 'test', 'description': 'test', 'path': '/skill.md'},
                       SOURCE_DIR='/tool', DIRECTORY='/analysis/{{TASK}}', BINARY='/original.so',
                       SHA256='a' * 64, IMAGE_BASE='0', PYTHON='python', OBJDUMP=None, READELF=None)
        for name in ('RECOVER.md', 'FEEDBACK.md'):
            with self.subTest(template=name):
                reference = render_prompt(prompts / name, **context, ASSEMBLY_ONLY=False)
                self.assertIn(code, reference)
                self.assertNotIn('```asm', reference)
                assembly = render_prompt(prompts / name, **context, ASSEMBLY_ONLY=True)
                self.assertIn('```asm\n4 ret\n```', assembly)
                self.assertIn('已有线索', assembly)
                self.assertNotIn(code, assembly)
                self.assertIn('/analysis/{{TASK}}', assembly)
                self.assertIn('edits=null', assembly)

    def test_guide_updates_preserve_custom_text_and_replace_only_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adaptation = root / 'adaptation.md'
            adaptation.write_text('自定义适配：{{TARGET}} 与 <JNI> 原样保留。', encoding='utf-8')
            guide = ensure(root / 'harness', '当前 SO', instructions=adaptation)
            original = guide.read_text(encoding='utf-8')
            self.assertIn('自定义适配：{{TARGET}} 与 <JNI> 原样保留。', original)
            record(root / 'harness', {'status': 'error', 'passed': False}, root / 'failed.json')
            record(root / 'harness', {'status': 'observed', 'passed': False}, root / 'observed.json')
            body = guide.read_text(encoding='utf-8')
            self.assertEqual(body.split(BEGIN)[0], original.split(BEGIN)[0])
            self.assertEqual(body.split(END)[1], original.split(END)[1])
            self.assertEqual(body.count(BEGIN), 1)
            self.assertIn('observed.json', body)
            self.assertNotIn('failed.json', body)

    def test_guide_with_missing_verification_section_retains_existing_instructions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            guide = root / 'HARNESS.md'
            guide.write_text('# 已有适配\n\n保留现有 ABI 与初始化记录。', encoding='utf-8')
            record(root, {'status': 'error', 'passed': False}, root / 'failed.json')
            body = guide.read_text(encoding='utf-8')
            self.assertTrue(body.startswith('# 已有适配\n\n保留现有 ABI 与初始化记录。'))
            self.assertEqual(body.count(BEGIN), 1)
            self.assertEqual(body.count(END), 1)
            self.assertIn('修复后需重新检查', body)


if __name__ == '__main__':
    unittest.main()
