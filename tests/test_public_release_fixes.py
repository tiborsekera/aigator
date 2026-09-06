"""Public release regressions using synthetic records and isolated storage."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from aigator.db import Database
from aigator.parsers import detect_and_parse

ROOT = Path(__file__).resolve().parents[1]


def payload(dom=False, texts=('question', 'answer'), date=None):
    result = {'source': 'chatgpt', 'id': 'release', 'title': 'Synthetic',
              'created_at': date or ('2026-09-06' if dom else '2026-09-01'),
              'turns': [{'author': 'user' if i % 2 == 0 else 'assistant', 'text': text}
                        for i, text in enumerate(texts)]}
    if dom:
        result['capture_kind'] = 'dom'
    return result


class PublicReleaseFixes(unittest.TestCase):
    def test_export_upgrades_capture_despite_older_source_time(self):
        db = Database(':memory:')
        db.upsert_session(detect_and_parse(payload(True, ('question',)))[0])
        official = detect_and_parse(payload())[0]
        official.messages[0].metadata['attachment'] = 'retained'
        self.assertEqual(db.upsert_session(official)[0], 'updated')
        stored = db.get_session(official.session_id)
        self.assertEqual(stored.updated_at, '2026-09-01T00:00:00')
        self.assertNotIn('capture_kind', stored.metadata)
        self.assertTrue(db.search('answer'))
        self.assertEqual(db.upsert_session(official)[0], 'unchanged')

    def test_capture_appends_only_matching_prefix_and_preserves_export(self):
        db = Database(':memory:')
        official = detect_and_parse(payload())[0]
        official.messages[0].metadata['attachment'] = 'retained'
        db.upsert_session(official)
        capture = detect_and_parse(payload(True, ('question', 'answer', 'followup')))[0]
        self.assertEqual(db.upsert_session(capture)[0], 'updated')
        stored = db.get_session(official.session_id)
        self.assertEqual(stored.messages[0].timestamp, official.messages[0].timestamp)
        self.assertEqual(stored.messages[0].metadata['attachment'], 'retained')
        self.assertEqual(stored.created_at, official.created_at)
        self.assertEqual(stored.metadata['export_updated_at'], official.updated_at)
        self.assertEqual(len(stored.messages), 3)
        self.assertEqual(db.upsert_session(capture)[0], 'unchanged')
        conflicting = detect_and_parse(payload(True, ('question', 'different', 'followup', 'reply')))[0]
        self.assertEqual(db.upsert_session(conflicting)[0], 'skipped_stale')
        self.assertFalse(db.search('different'))

    def test_export_refresh_preserves_captured_tail_then_replaces_it(self):
        db = Database(':memory:')
        official = detect_and_parse(payload())[0]
        db.upsert_session(official)
        db.upsert_session(detect_and_parse(payload(True, ('question', 'answer', 'followup')))[0])
        official.metadata['new_detail'] = 'export detail'
        self.assertEqual(db.upsert_session(official)[0], 'updated')
        self.assertTrue(db.search('followup'))
        complete = detect_and_parse(payload(False, ('question', 'answer', 'followup', 'reply'), '2026-09-02'))[0]
        self.assertEqual(db.upsert_session(complete)[0], 'updated')
        self.assertEqual(db.get_session(complete.session_id).updated_at, '2026-09-02T00:00:00')
        self.assertEqual(db.upsert_session(official)[0], 'skipped_stale')
        self.assertTrue(db.search('reply'))

    def test_shorter_export_preserves_matching_capture_tail(self):
        db = Database(':memory:')
        db.upsert_session(detect_and_parse(payload(True, ('question', 'answer', 'followup')))[0])
        official = detect_and_parse(payload())[0]
        self.assertEqual(db.upsert_session(official)[0], 'updated')
        self.assertTrue(db.search('followup'))
        stored = db.get_session(official.session_id)
        self.assertNotIn('capture_kind', stored.metadata)
        self.assertEqual(stored.messages[0].timestamp, official.messages[0].timestamp)

    def test_claude_tools_and_attachments_survive_batch_storage(self):
        data = {'uuid': 'claude-release', 'name': 'Synthetic', 'chat_messages': [
            {'uuid': 'u', 'sender': 'human', 'text': 'read file', 'attachments': [
                {'file_name': 'synthetic.txt', 'extracted_content': 'attachmentneedle'}]},
            {'uuid': 'a', 'sender': 'assistant', 'content': [
                {'type': 'tool_use', 'id': 't', 'name': 'bash_tool', 'input': {'command': 'echo synthetic'}}]},
            {'uuid': 't', 'sender': 'tool', 'attachments': None, 'content': [
                {'type': 'tool_result', 'tool_use_id': 't', 'content': 'resultneedle'}]}]}
        session = detect_and_parse([data])[0]
        db = Database(':memory:')
        db.upsert_session(session)
        stored = db.get_session(session.session_id)
        call = stored.messages[1].tool_calls[0]
        self.assertEqual(call.arguments, {'command': 'echo synthetic'})
        self.assertEqual(call.result, 'resultneedle')
        self.assertTrue(db.search('attachmentneedle'))
        self.assertTrue(db.search('resultneedle'))
        self.assertEqual(json.loads(stored.raw_payload), data)

    def test_wrappers_source_override_and_takeout_grouping(self):
        cases = [('chatgpt', 'conversations', 'chatgpt_sample.json'),
                 ('claude_web', 'conversations', 'claude_sample.json'),
                 ('perplexity', 'threads', 'perplexity_sample.json'),
                 ('gemini_web', 'chats', 'gemini_sample.json')]
        for source, wrapper, filename in cases:
            data = json.loads((ROOT / 'tests/fixtures' / filename).read_text())
            self.assertTrue(detect_and_parse({wrapper: data}, source_hint=source)[0].messages)
            if source != 'gemini_web':
                self.assertTrue(detect_and_parse({wrapper: data})[0].messages)
        activity = [{'header': 'Gemini Apps', 'title': 'Prompted question',
                     'time': '2026-09-01', 'details': [{'url': 'https://gemini.google.com/app/test'}]}] * 2
        self.assertEqual(len(detect_and_parse(activity, source_hint='gemini_web')), 1)
        with self.assertRaisesRegex(ValueError, 'Unknown source'):
            detect_and_parse(payload(), source_hint='typo')
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            detect_and_parse(payload(), source_hint='claude_web')
        with self.assertRaisesRegex(ValueError, 'Unrecognized'):
            detect_and_parse({'email': 'synthetic@example.invalid'}, source_hint='chatgpt')

    def test_empty_chatgpt_message_accepts_null_metadata(self):
        data = {'id': 'null', 'mapping': {'m': {'message': {
            'author': {'role': 'assistant'}, 'content': {'parts': []}, 'metadata': None}}}}
        self.assertEqual(detect_and_parse(data)[0].messages, [])
        activity = [{'header': 'Gemini Apps', 'title': 'Prompted question',
                     'details': None, 'safeHtmlItem': None}]
        self.assertEqual(detect_and_parse(activity)[0].messages[0].content, 'question')

    def test_cli_legacy_encoding_import_and_unicode_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = [sys.executable, '-m', 'aigator.cli', '--db', str(Path(tmp) / 'index.db')]
            env = {**os.environ, 'PYTHONIOENCODING': 'cp1252'}
            result = subprocess.run(base + ['import', str(ROOT / 'tests/fixtures/chatgpt_sample.json')],
                                    env=env, cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(base + ['list', '--json'], env=env, cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(result.stdout)), 1)
