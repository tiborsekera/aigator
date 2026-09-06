"""Synthetic regressions for the public-release review."""
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from aigator.db import Database
from aigator.models import CanonicalSession, CanonicalMessage, normalize_iso_datetime, make_session_id
from aigator.parsers import detect_and_parse
from aigator.server.daemon import CopilotLiveWatcher, run_daemon
from aigator.cli import import_zip_file

ROOT = Path(__file__).resolve().parents[1]


def session(text='partial', updated='2026-09-06T08:00:00Z'):
    return CanonicalSession.create('chatgpt', 'regression', 'Test', created_at='2026-09-01T08:00:00Z', updated_at=updated,
        messages=[CanonicalMessage('m1', 0, 'assistant', text, timestamp='2026-09-01T08:00:00Z')])


class ReleaseRegressions(unittest.TestCase):
    def test_offsets_and_precision(self):
        self.assertEqual(normalize_iso_datetime('2026-07-01T10:00:00.123456+02:00'), normalize_iso_datetime('2026-07-01T08:00:00.123456Z'))
        self.assertEqual(normalize_iso_datetime('2026-01-01T09:00:00+01:00'), normalize_iso_datetime('2026-01-01T08:00:00Z'))
        self.assertEqual(normalize_iso_datetime('2026-01-01T08:00:00'), normalize_iso_datetime('2026-01-01T08:00:00Z'))

    def test_updates_preserve_source_time_and_completion(self):
        db = Database(':memory:')
        first = session()
        self.assertEqual(first.updated_at, '2026-09-06T08:00:00')
        db.upsert_session(first)
        complete = session('completed response')
        self.assertEqual(db.upsert_session(complete)[0], 'updated')
        self.assertEqual(db.upsert_session(complete)[0], 'unchanged')
        self.assertTrue(db.search('completed'))
        self.assertEqual(db.upsert_session(session('old', '2026-09-05T08:00:00Z'))[0], 'skipped_stale')
        self.assertEqual(db.get_session(first.session_id).messages[0].content, 'completed response')

    def test_export_survives_dom_capture(self):
        db = Database(':memory:')
        original = session('rich export')
        original.messages[0].metadata = {'attachment': 'details'}
        db.upsert_session(original)
        capture = session('lossy', '2026-09-07T08:00:00Z')
        capture.metadata['capture_kind'] = 'dom'
        self.assertEqual(db.upsert_session(capture)[0], 'skipped_stale')
        self.assertEqual(db.get_session(original.session_id).messages[0].metadata, {'attachment': 'details'})

    def test_exact_capture_schemas_search(self):
        for source in ('chatgpt', 'claude_web', 'gemini_web', 'perplexity'):
            with self.subTest(source=source):
                payload = {'source': source, 'id': 'browser', 'title': 'Capture', 'capture_kind': 'dom',
                    'created_at': '2026-09-06T08:00:00Z', 'turns': [
                        {'author': 'user', 'text': 'findneedle question'}, {'author': 'assistant', 'text': 'findneedle answer'}]}
                parsed = detect_and_parse(payload)[0]
                self.assertEqual([m.role for m in parsed.messages], ['user', 'assistant'])
                self.assertEqual(parsed.metadata['capture_kind'], 'dom')
                db = Database(':memory:')
                db.upsert_session(parsed)
                self.assertEqual(len(db.search('findneedle')), 2)

    def test_gemini_prompt_untruncated(self):
        prompt = 'Prompted ' + 'x' * 160 + '\n```python\nprint(42)\n```'
        live = detect_and_parse({'source': 'gemini_web', 'turns': [{'author': 'user', 'text': prompt}]})[0]
        self.assertEqual(live.messages[0].content, prompt)
        takeout = detect_and_parse([{'header': 'Gemini Apps', 'title': 'Prompted ' + prompt, 'time': '2026-09-06T08:00:00Z'}])[0]
        self.assertEqual(takeout.messages[0].content, prompt)
        self.assertLessEqual(len(takeout.title), 100)

    def test_ambiguous_gemini_mention(self):
        with self.assertRaises(ValueError):
            detect_and_parse([{'title': 'Talking about Gemini', 'turns': [{'text': 'Gemini'}]}])

    def test_selected_chatgpt_branch(self):
        def node(parent, text, ts):
            return {'parent': parent, 'message': {'author': {'role': 'assistant'}, 'content': {'parts': [text]}, 'create_time': ts}}
        payload = {'id': 'branch', 'mapping': {'root': node(None, 'question', 1), 'old': node('root', 'old answer', 2), 'new': node('root', 'selected answer', 3)}, 'current_node': 'new'}
        parsed = detect_and_parse(payload)[0]
        self.assertEqual([m.content for m in parsed.messages], ['question', 'selected answer'])
        self.assertIn('old', parsed.metadata['branch_mapping'])

    def test_hostile_ids_and_roles(self):
        self.assertNotIn('"', make_session_id('chatgpt', 'review"><img onerror=alert(1)>'))
        with self.assertRaises(ValueError):
            CanonicalSession.from_dict({'messages': [{'role': '<img src=x onerror=alert(1)>', 'content': 'bad'}]})

    def test_remote_binding_rejected_before_db_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'should-not-exist.db'
            with self.assertRaises(ValueError):
                run_daemon(host='0.0.0.0', db_path=path)
            self.assertFalse(path.exists())

    @unittest.skipUnless(os.name == 'posix', 'POSIX modes')
    def test_private_database_and_sidecars(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'private' / 'chat.db'
            db = Database(path)
            with db._get_connection() as conn:
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                for name in (path, Path(str(path) + '-wal'), Path(str(path) + '-shm')):
                    if name.exists():
                        self.assertEqual(name.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute('SELECT 1')

    def test_wal_watcher_retries(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'copilot.db'
            conn = sqlite3.connect(path)
            try:
                conn.execute('PRAGMA journal_mode=WAL')
                conn.executescript('CREATE TABLE sessions(id TEXT, summary TEXT, created_at TEXT, updated_at TEXT); CREATE TABLE turns(session_id TEXT, turn_index INTEGER, user_message TEXT, assistant_response TEXT, timestamp TEXT);')
                conn.execute("INSERT INTO sessions VALUES ('s', 'WAL', '2026-09-01', '2026-09-06')")
                conn.execute("INSERT INTO turns VALUES ('s', 0, 'first', 'reply', '2026-09-06')")
                conn.commit()
                watcher = CopilotLiveWatcher(Database(':memory:'))
                with patch('aigator.server.daemon.COPILOT_DB_DEFAULT', path):
                    watcher.sync_once()
                    original_mtime = path.stat().st_mtime_ns
                    conn.execute("INSERT INTO turns VALUES ('s', 1, 'walneedle', 'second reply', '2026-09-06')")
                    conn.commit()
                    self.assertEqual(original_mtime, path.stat().st_mtime_ns)
                    previous = watcher.last_version
                    with patch('aigator.server.daemon.VSCodeCopilotParser.parse_db_file', side_effect=sqlite3.OperationalError('busy')):
                        with self.assertRaises(sqlite3.OperationalError):
                            watcher.sync_once()
                    self.assertEqual(previous, watcher.last_version)
                    watcher.sync_once()
                    self.assertTrue(watcher.db.search('walneedle'))
            finally:
                conn.close()

    def test_zip_no_json_and_oversized(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'empty.zip'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('activity.html', '<p>unsupported</p>')
            with self.assertRaisesRegex(ValueError, 'No importable'):
                import_zip_file(path)
            info = zipfile.ZipInfo('conversations.json')
            info.file_size = 51 * 1024 * 1024
            with patch.object(zipfile.ZipFile, 'infolist', return_value=[info]):
                with self.assertRaisesRegex(ValueError, 'limits'):
                    import_zip_file(path)

    def test_zip_ignores_account_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'export.zip'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('user.json', '{"email": "synthetic@example.invalid"}')
                z.writestr('conversations.json', json.dumps({'source': 'perplexity', 'turns': [{'author': 'user', 'text': 'question'}]}))
            self.assertEqual(len(import_zip_file(path)), 1)

    def test_legacy_id_does_not_duplicate(self):
        db = Database(':memory:')
        old = session()
        old.native_id = 'legacy id'
        old.session_id = 'chatgpt_legacy id'
        db.upsert_session(old)
        updated = session('completed')
        updated.native_id = old.native_id
        updated.session_id = make_session_id('chatgpt', old.native_id)
        db.upsert_session(updated)
        self.assertEqual(db.get_stats()['total_sessions'], 1)
        self.assertEqual(db.get_session(old.session_id).messages[0].content, 'completed')

    def test_missing_import_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([os.sys.executable, '-m', 'aigator.cli', '--db', str(Path(temp) / 'db'), 'import', str(Path(temp) / 'missing.json')], cwd=ROOT, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'Import incomplete', result.stderr)
