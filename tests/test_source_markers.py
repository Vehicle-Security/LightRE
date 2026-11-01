"""Small source fixtures only: no analyzer, model, browser or Web service."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from recovery.markers import begin, end, parse


class SourceMarkerTests(unittest.TestCase):
    metadata = {'v': 1, 'id': 'f1', 'origins': ['0x4']}

    def parse(self, text):
        return parse(text, lambda rva: '')

    def test_literals_comment_examples_and_splicing_are_not_markers(self):
        body = '''// 普通说明
const char *s = "// @lightre.end {\\"id\\":\\"f1\\"}";
const char *raw = u8R"tag(
// @lightre.begin {"v":1,"id":"fake","origins":[]}
)tag";
/* 示例：
// @lightre.end {"id":"f1"}
*/
// escaped comment \\
// @lightre.end {"id":"f1"}
const int count = 1'000;
'''
        text = begin(self.metadata) + '\n' + body + '\n' + end('f1')
        blocks = self.parse(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].body, body)

    def test_crlf_indentation_unicode_and_json_field_order(self):
        body = '// 中文解释\r\nint renamed(void) { return 1; }\r\n'
        text = (' \t// @lightre.begin { "origins": ["0x4"], "id": "函数一", "v": 1 }\r\n\r\n' +
                body + '\r\n\t// @lightre.end { "id": "函数一" }\r\n')
        block, = self.parse(text)
        self.assertEqual(block.metadata['id'], '函数一')
        self.assertEqual(block.body, body)
        self.assertEqual(text[block.start():block.end()], block.group())

    def test_raw_string_splice_does_not_create_a_false_terminator(self):
        body = 'const char *s = R"tag()tag\\\n"\n// @lightre.end {"id":"fake"}\n)tag";'
        block, = self.parse(begin(self.metadata) + '\n' + body + '\n' + end('f1'))
        self.assertEqual(block.body, body)

    def test_invalid_schema_and_boundaries_fail_closed(self):
        invalid = [
            '{"v":1,"v":1,"id":"f1","origins":["0x4"]}',
            '{"v":true,"id":"f1","origins":["0x4"]}',
            '{"v":2,"id":"f1","origins":["0x4"]}',
            '{"v":1,"id":"","origins":["0x4"]}',
            '{"v":1,"id":"f1","origins":["0x4","0x04"]}',
            '{"v":1,"id":"f1","origins":[4]}',
            '{"v":1,"id":"f1","origins":["0x4"],"status":NaN}',
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.parse('// @lightre.begin ' + payload + '\nint f();\n' + end('f1'))
        block = begin(self.metadata) + '\nint f();\n' + end('f1') + '\n'
        for text in (block + block, begin(self.metadata), end('f1'),
                     begin(self.metadata) + begin(self.metadata) + end('f1'),
                     begin(self.metadata) + 'int f();\n' + end('other'),
                     'int f(); ' + block, '// @lightre.begin {broken}\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.parse(text)


if __name__ == '__main__':
    unittest.main()
