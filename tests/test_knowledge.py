import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from recovery import knowledge_rvas, migrate_knowledge, read_knowledge, save_knowledge, save_knowledge_batch
from recovery import knowledge


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.reader = SimpleNamespace(directory=Path(temporary.name), catalog=lambda: {
            'sample': {'source_path': '/inputs/sample.so', 'sha256': 'a' * 64}})

    def test_parser_handles_code_fences_quotes_setext_and_preserves_other_sections(self):
        path = self.reader.directory / 'knowledge.md'
        text = ('# 人工说明\n\n开头原文。\n\n## 0x00000004 first\n\n### 事实\n'
                '```markdown\n## 0x000000ff\n```\n\n> ## 0x000000aa\n\n'
                '0x00000008 second\n-----------------\n\n第二个函数原文。\n\n'
                '## 全局说明\n\n尾部原文。\n')
        path.write_text(text)
        self.assertEqual(knowledge_rvas(self.reader), {4, 8})
        self.assertIn('## 0x000000ff', read_knowledge(self.reader, 4))
        second = read_knowledge(self.reader, 8)
        save_knowledge(self.reader, 4, '### 新事实\n\n更新第一项。')
        save_knowledge(self.reader, 6, '新函数。')
        self.assertEqual(read_knowledge(self.reader, 8), second)
        updated = path.read_text()
        self.assertTrue(updated.startswith('# 人工说明\n\n开头原文。'))
        self.assertTrue(updated.endswith('## 全局说明\n\n尾部原文。\n'))
        self.assertLess(updated.index('## 0x00000006'), updated.index('0x00000008 second'))

    def test_notes_headings_are_nested_under_function_and_fences_are_unchanged(self):
        notes = '# 结论\n\n## 依据\n\n```c\n## keep_in_code\n```\n'
        save_knowledge(self.reader, 4, notes)
        saved = read_knowledge(self.reader, 4)
        self.assertIn('### 结论', saved)
        self.assertIn('#### 依据', saved)
        self.assertIn('```c\n## keep_in_code\n```', saved)
        self.assertEqual(knowledge_rvas(self.reader), {4})

    def test_migration_consolidates_files_and_can_resume_after_publication(self):
        directory = self.reader.directory / 'knowledge'
        directory.mkdir()
        (directory / '0x00000004.md').write_text('### 事实\n\n第一项。\n')
        (directory / '0x00000008.md').write_text('## 未知\n\n第二项。')
        self.assertEqual(migrate_knowledge(self.reader), 2)
        self.assertEqual(read_knowledge(self.reader, 4), '### 事实\n\n第一项。')
        self.assertEqual(read_knowledge(self.reader, 8), '### 未知\n\n第二项。')
        self.assertFalse(directory.exists())
        before = (self.reader.directory / 'knowledge.md').read_bytes()
        directory.mkdir()
        (directory / '0x00000004.md').write_text('### 事实\n\n第一项。\n')
        self.assertEqual(migrate_knowledge(self.reader), 1)
        self.assertEqual((self.reader.directory / 'knowledge.md').read_bytes(), before)
        self.assertEqual(migrate_knowledge(self.reader), 0)

    def test_failed_migration_preserves_all_legacy_files(self):
        directory = self.reader.directory / 'knowledge'
        directory.mkdir()
        old = directory / '0x00000004.md'
        old.write_text('保留事实。')
        with patch('recovery.knowledge._atomic_write', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                migrate_knowledge(self.reader)
        self.assertEqual(old.read_text(), '保留事实。')
        self.assertFalse((self.reader.directory / 'knowledge.md').exists())

    def test_conflicts_and_duplicate_function_headers_are_not_silently_overwritten(self):
        path = save_knowledge(self.reader, 4, '新版事实。')
        directory = self.reader.directory / 'knowledge'
        directory.mkdir()
        old = directory / '0x00000004.md'
        old.write_text('另一份事实。')
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, '冲突'):
            migrate_knowledge(self.reader)
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(old.exists())
        old.unlink()
        path.write_text('## 0x4\n\n甲\n\n## 0x00000004\n\n乙\n')
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, '重复函数标题'):
            save_knowledge(self.reader, 8, '新内容。')
        self.assertEqual(path.read_bytes(), before)

    def test_concurrent_function_updates_share_one_file_without_lost_entries(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda rva: save_knowledge(self.reader, rva, f'### 事实\n\n函数 {rva}'), range(24)))
        self.assertEqual(knowledge_rvas(self.reader), set(range(24)))
        for rva in range(24):
            self.assertEqual(read_knowledge(self.reader, rva), f'### 事实\n\n函数 {rva}')
        self.assertEqual([path.name for path in self.reader.directory.glob('*.md')], ['knowledge.md'])

    def test_reading_missing_knowledge_does_not_create_a_document(self):
        self.assertEqual(read_knowledge(self.reader, 4), '')
        self.assertEqual(knowledge_rvas(self.reader), set())
        self.assertFalse((self.reader.directory / 'knowledge.md').exists())

    def test_unclosed_markdown_cannot_swallow_other_function_entries(self):
        path = save_knowledge(self.reader, 8, '需要保留的其他函数。')
        before = path.read_bytes()
        for rva in (4, 12):
            with self.assertRaises(ValueError):
                save_knowledge(self.reader, rva, '```c\n未闭合的代码块')
            self.assertEqual(path.read_bytes(), before)

    def test_repeated_reads_reuse_memory_until_an_external_edit_or_delete(self):
        path = self.reader.directory / 'knowledge.md'
        path.write_text('## 0x4\n\n原文。\n')
        with patch('recovery.knowledge._sections', wraps=knowledge._sections) as parse, \
             patch.object(Path, 'read_text', autospec=True, side_effect=Path.read_text) as read:
            for _ in range(5):
                self.assertEqual(read_knowledge(self.reader, 4), '原文。')
                self.assertEqual(knowledge_rvas(self.reader), {4})
            self.assertEqual(parse.call_count, 1)
            self.assertEqual(read.call_count, 1)
            replacement = path.with_suffix('.new')
            replacement.write_text('## 0x4\n\n新文。\n')
            replacement.replace(path)
            self.assertEqual(read_knowledge(self.reader, 4), '新文。')
            self.assertEqual(parse.call_count, 2)
            self.assertEqual(read.call_count, 2)
            path.unlink()
            self.assertEqual(read_knowledge(self.reader, 4), '')
            self.assertEqual(knowledge_rvas(self.reader), set())
            path.write_text('## 0x8\n\n重新创建。\n')
            self.assertEqual(read_knowledge(self.reader, 8), '重新创建。')

    def test_batch_saves_once_and_reuses_memory_without_redundant_writes(self):
        save_knowledge(self.reader, 8, '保留。')
        with patch('recovery.knowledge._atomic_write', wraps=knowledge._atomic_write) as write, \
             patch.object(Path, 'read_text', autospec=True, side_effect=Path.read_text) as read:
            updates = {12: '十二。', 4: '四。', 8: '更新。', 16: '十六。'}
            path = save_knowledge_batch(self.reader, updates)
            self.assertEqual(write.call_count, 1)
            for rva, notes in updates.items():
                self.assertEqual(read_knowledge(self.reader, rva), notes)
            self.assertEqual(read.call_count, 0)
            save_knowledge_batch(self.reader, updates)
            self.assertEqual(write.call_count, 1)
        headings = [line for line in path.read_text().splitlines() if line.startswith('## ')]
        self.assertEqual(headings, [f'## {rva:#010x}' for rva in (4, 8, 12, 16)])

    def test_batch_merges_external_changes_and_failed_save_keeps_memory_and_file(self):
        path = save_knowledge(self.reader, 4, '旧文。')
        path.write_text(path.read_text().replace('旧文。', '人工修改。') + '\n## 0x8\n\n另一项。\n')
        save_knowledge_batch(self.reader, {12: '新增。'})
        self.assertEqual(read_knowledge(self.reader, 4), '人工修改。')
        self.assertEqual(read_knowledge(self.reader, 8), '另一项。')
        before = path.read_bytes()
        with patch('recovery.knowledge._atomic_write', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                save_knowledge_batch(self.reader, {4: '不应生效。', 16: '也不生效。'})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(read_knowledge(self.reader, 4), '人工修改。')
        self.assertNotIn(16, knowledge_rvas(self.reader))
