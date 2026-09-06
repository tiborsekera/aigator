"""Literal fuzzy search, indexing lifecycle, and relevance regressions."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from aigator.db import Database
from aigator.models import CanonicalMessage, CanonicalSession


def session(native_id, title='Discussion', content='sqlite search', source='codex', date='2026-09-01'):
    return CanonicalSession(session_id=source + '_' + native_id, source=source, native_id=native_id, title=title,
                            created_at=date, updated_at=date, messages=[
                                CanonicalMessage(id='m1', turn_index=0, role='user',
                                                 content=content, timestamp=date)])


class FuzzySearchTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(':memory:')

    def test_subsequence_and_reordered_tokens_with_exact_mode_unchanged(self):
        self.db.upsert_session(session('one'))
        self.assertFalse(self.db.search('sqlt'))
        self.assertEqual(len(self.db.search('srch sqlt', fuzzy=True)), 1)
        hit = self.db.search('sqlt', fuzzy=True)[0]
        self.assertEqual(hit.snippet.replace('«', '').replace('»', ''), 'sqlite search')
        self.assertIn('«', hit.snippet)
        self.assertFalse(self.db.search('sqilte', fuzzy=True))  # Not edit distance.

    def test_rank_exact_body_above_gappy_title_and_old_records_not_cut_off(self):
        self.db.upsert_session(session('old', title='Unrelated', content='sql', date='2001-01-01'))
        self.db.upsert_session(session('new', title='sequel', content='unrelated'))
        self.assertEqual(self.db.search('sql', fuzzy=True)[0].session_id, 'codex_old')
        self.db.upsert_session(session('title', title='sql', content='unrelated'))
        self.assertEqual(self.db.search('sql', fuzzy=True)[0].session_id, 'codex_title')

    def test_unicode_case_accents_and_literal_punctuation(self):
        self.db.upsert_session(session('unicode', content='Straße CAFÉ C++ 100% a_b <script>'))
        for query in ('STRASSE', 'cafe', 'C++', '100%', 'a_b', '<script>'):
            self.assertTrue(self.db.search(query, fuzzy=True), query)
        self.assertFalse(self.db.search("' OR 1=1 --", fuzzy=True))
        self.assertFalse(self.db.search('100?', fuzzy=True))

    def test_title_only_matches_explain_why_the_result_matched(self):
        self.db.upsert_session(session('title', title='SQLite search', content='A local archive.'))
        hit = self.db.search('sql srch', fuzzy=True)[0]
        self.assertTrue(hit.snippet.startswith('Title: '))
        self.assertIn('«', hit.snippet)
        self.assertEqual(hit.snippet.replace('«', '').replace('»', ''), 'Title: SQLite search')

    def test_weak_title_does_not_mask_exact_body(self):
        self.db.upsert_session(session('body', title='sequel', content='sql search'))
        self.db.upsert_session(session('weak', title='sequel', content='unrelated'))
        hits = self.db.search('sql', fuzzy=True)
        self.assertEqual(hits[0].session_id, 'codex_body')
        self.assertIn('«sql»', hits[0].snippet)
        self.assertEqual(self.db.search('sql srch', fuzzy=True)[0].session_id, 'codex_body')

    def test_sources_pagination_and_input_limits(self):
        for i in range(4):
            self.db.upsert_session(session(str(i), source='codex' if i < 3 else 'claude_code'))
        all_hits = self.db.search('sql', sources=['codex'], fuzzy=True)
        page = self.db.search('sql', sources=['codex'], fuzzy=True, offset=1, limit=1)
        self.assertEqual(page[0].session_id, all_hits[1].session_id)
        self.assertEqual(len(all_hits), 3)
        for query in ('', '  ', 'a' * 257, 'a' * 65, 'a b c d e f g h i'):
            self.assertFalse(self.db.search(query, fuzzy=True))

    def test_no_matches_spanning_paragraphs_or_unbounded_gaps(self):
        for i, content in enumerate(('s\nq\nl', 's' + 'x' * 500 + 'ql',
                                     'alpha\n\nbeta', 'alpha ' + 'x' * 500 + ' beta')):
            self.db.upsert_session(session(str(i), content=content))
        self.assertFalse(self.db.search('sql', fuzzy=True))
        self.assertFalse(self.db.search('alpha beta', fuzzy=True))

    def test_update_delete_and_existing_database_backfill(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'archive.db'
            db = Database(path)
            db.upsert_session(session('one', content='oldword'))
            with closing(sqlite3.connect(path)) as conn:
                conn.execute('DROP TABLE fuzzy_index')
                conn.commit()
            db = Database(path)
            self.assertTrue(db.search('oldwrd', fuzzy=True))
            db.upsert_session(session('one', content='newword', date='2026-09-02'))
            self.assertFalse(db.search('oldwrd', fuzzy=True))
            self.assertTrue(db.search('newwrd', fuzzy=True))
            db.delete_session('codex_one')
            self.assertFalse(db.search('newwrd', fuzzy=True))


if __name__ == '__main__':
    unittest.main()
