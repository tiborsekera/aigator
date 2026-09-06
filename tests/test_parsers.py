"""Unit tests for Aigator parsers and DB operations."""

import sys
from pathlib import Path
import unittest

# Ensure parent directory is in sys.path for test execution & linters
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from aigator import Database, Role, Source
from aigator.parsers import detect_and_parse


class TestParsers(unittest.TestCase):
    def test_chatgpt_parser(self):
        chatgpt_sample = [
            {
                "id": "chat-uuid-1",
                "title": "Quantum Computing Fundamentals",
                "create_time": 1700000000.0,
                "update_time": 1700000100.0,
                "mapping": {
                    "node-1": {
                        "id": "node-1",
                        "message": {
                            "id": "msg-1",
                            "author": {"role": "user"},
                            "create_time": 1700000005.0,
                            "content": {"content_type": "text", "parts": ["Explain qubits simply."]},
                        },
                    },
                    "node-2": {
                        "id": "node-2",
                        "message": {
                            "id": "msg-2",
                            "author": {"role": "assistant"},
                            "create_time": 1700000015.0,
                            "content": {"content_type": "text", "parts": ["A qubit is a quantum superposition of 0 and 1."]},
                        },
                    },
                },
            }
        ]
        sessions = detect_and_parse(chatgpt_sample)
        self.assertEqual(len(sessions), 1)
        sess = sessions[0]
        self.assertEqual(sess.source, Source.CHATGPT.value)
        self.assertEqual(sess.title, "Quantum Computing Fundamentals")
        self.assertEqual(len(sess.messages), 2)
        self.assertEqual(sess.messages[0].role, Role.USER.value)
        self.assertEqual(sess.messages[0].content, "Explain qubits simply.")
        self.assertEqual(sess.messages[1].role, Role.ASSISTANT.value)

    def test_claude_parser(self):
        claude_sample = [
            {
                "uuid": "claude-uuid-99",
                "name": "Rust Async Runtime",
                "created_at": "2024-04-01T10:00:00Z",
                "updated_at": "2024-04-01T10:05:00Z",
                "chat_messages": [
                    {
                        "uuid": "m-c-1",
                        "sender": "human",
                        "text": "How does Tokio work under the hood?",
                        "created_at": "2024-04-01T10:00:10Z",
                    },
                    {
                        "uuid": "m-c-2",
                        "sender": "assistant",
                        "text": "Tokio uses an epoll/kqueue based reactor and work-stealing thread pool.",
                        "created_at": "2024-04-01T10:00:30Z",
                    },
                ],
            }
        ]
        sessions = detect_and_parse(claude_sample)
        self.assertEqual(len(sessions), 1)
        sess = sessions[0]
        self.assertEqual(sess.source, Source.CLAUDE_WEB.value)
        self.assertEqual(sess.title, "Rust Async Runtime")
        self.assertEqual(len(sess.messages), 2)
        self.assertEqual(sess.messages[0].content, "How does Tokio work under the hood?")

    def test_gemini_parser(self):
        gemini_sample = [
            {
                "id": "gemini-conv-42",
                "source": "gemini_web",
                "title": "Gemini Architecture",
                "created_at": "2024-05-10T12:00:00Z",
                "turns": [
                    {"author": "user", "text": "What is Gemini 1.5 Pro?", "timestamp": "2024-05-10T12:00:05Z"},
                    {"author": "model", "text": "Gemini 1.5 Pro features a 2M token context window.", "timestamp": "2024-05-10T12:00:15Z"},
                ],
            }
        ]
        sessions = detect_and_parse(gemini_sample)
        self.assertEqual(len(sessions), 1)
        sess = sessions[0]
        self.assertEqual(sess.source, Source.GEMINI_WEB.value)
        self.assertEqual(len(sess.messages), 2)

    def test_perplexity_parser(self):
        pplx_sample = [
            {
                "thread_id": "pplx-thread-777",
                "title": "PostgreSQL 17 Indexing Features",
                "created_at": "2024-06-01T08:00:00Z",
                "entries": [
                    {
                        "query": "What are the new indexing performance improvements in PostgreSQL 17?",
                        "answer": "PostgreSQL 17 introduces improved memory management for VACUUM and B-tree index builds.",
                        "sources": [{"name": "PostgreSQL Docs", "url": "https://www.postgresql.org/docs/17/"}],
                    }
                ],
            }
        ]
        sessions = detect_and_parse(pplx_sample)
        self.assertEqual(len(sessions), 1)
        sess = sessions[0]
        self.assertEqual(sess.source, Source.PERPLEXITY.value)
        self.assertEqual(len(sess.messages), 2)
        self.assertIn("PostgreSQL 17", sess.messages[1].content)
        self.assertIn("PostgreSQL Docs", sess.messages[1].content)

    def test_gemini_takeout_activity_parser(self):
        activity_sample = [
            {
                "header": "Gemini Apps",
                "title": "Prompted How to build a custom PyTorch dataset loader",
                "time": "2026-09-04T15:41:37.957Z",
                "details": [{"url": "https://gemini.google.com/app/17ddfa59f81b0930"}],
                "safeHtmlItem": [{"html": "<p>Inherit from <strong>torch.utils.data.Dataset</strong> and implement __len__ and __getitem__.</p>"}],
            },
            {
                "header": "Gemini Apps",
                "title": "Prompted How to use DataLoader with multiprocessing",
                "time": "2026-09-04T15:43:00.000Z",
                "details": [{"url": "https://gemini.google.com/app/17ddfa59f81b0930"}],
                "safeHtmlItem": [{"html": "<p>Set num_workers > 0 in your DataLoader instance.</p>"}],
            },
        ]
        sessions = detect_and_parse(activity_sample)
        self.assertEqual(len(sessions), 1)
        sess = sessions[0]
        self.assertEqual(sess.source, Source.GEMINI_WEB.value)
        self.assertEqual(sess.title, "How to build a custom PyTorch dataset loader")
        self.assertEqual(len(sess.messages), 4)
        self.assertEqual(sess.messages[0].content, "How to build a custom PyTorch dataset loader")
        self.assertIn("**torch.utils.data.Dataset**", sess.messages[1].content)
        self.assertEqual(sess.messages[2].content, "How to use DataLoader with multiprocessing")
        self.assertIn("num_workers > 0", sess.messages[3].content)

    def test_claude_tool_blocks_formatting(self):
        claude_tool_sample = [
            {
                "uuid": "claude-tool-uuid",
                "name": "Documentation Search",
                "created_at": "2026-09-01T08:15:43Z",
                "chat_messages": [
                    {
                        "uuid": "m1",
                        "sender": "human",
                        "text": "What is the latest syntax for Rust async closures?",
                    },
                    {
                        "uuid": "m2",
                        "sender": "assistant",
                        "text": "Checking...\n```\nThis block is not supported on your current device yet.\n```\nAsync closures use async || syntax in Rust 2024.",
                        "content": [
                            {"type": "text", "text": "Checking..."},
                            {"type": "tool_use", "name": "web_search", "input": {"query": "Rust async closures 2024 edition"}},
                            {"type": "text", "text": "Async closures use async || syntax in Rust 2024."},
                        ],
                    },
                ],
            }
        ]
        sessions = detect_and_parse(claude_tool_sample)
        self.assertEqual(len(sessions), 1)
        sess = sessions[0]
        self.assertNotIn("This block is not supported", sess.messages[1].content)
        self.assertIn("🔍 *[Search: Rust async closures 2024 edition]*", sess.messages[1].content)
        self.assertIn("Async closures use async || syntax in Rust 2024.", sess.messages[1].content)


class TestDatabaseFTS(unittest.TestCase):
    def test_db_fts_search(self):
        db = Database(":memory:")
        chatgpt_sample = [
            {
                "id": "c1",
                "title": "FastAPI Web Architecture",
                "create_time": 1700000000.0,
                "mapping": {
                    "n1": {
                        "id": "n1",
                        "message": {
                            "id": "m1",
                            "author": {"role": "user"},
                            "content": {"parts": ["How to write clean async endpoints in FastAPI?"]},
                        },
                    },
                    "n2": {
                        "id": "n2",
                        "message": {
                            "id": "m2",
                            "author": {"role": "assistant"},
                            "content": {"parts": ["Use async def and dependency injection with Depends."]},
                        },
                    },
                },
            }
        ]
        sessions = detect_and_parse(chatgpt_sample)
        for s in sessions:
            db.upsert_session(s)

        results = db.search("FastAPI endpoints")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].title, "FastAPI Web Architecture")
        self.assertIn("FastAPI", results[0].snippet)

    def test_db_deduplication_and_extension(self):
        db = Database(":memory:")
        sample_v1 = [
            {
                "uuid": "session-ext-1",
                "name": "Progressive Chat",
                "created_at": "2026-09-01T10:00:00Z",
                "chat_messages": [
                    {"uuid": "m1", "sender": "human", "text": "Hello 1"},
                    {"uuid": "m2", "sender": "assistant", "text": "Hi 1"},
                ],
            }
        ]
        s1 = detect_and_parse(sample_v1)[0]
        status, diff = db.upsert_session(s1)
        self.assertEqual(status, "inserted")
        self.assertEqual(diff, 2)

        # Re-import identical: should be unchanged
        status2, diff2 = db.upsert_session(s1)
        self.assertEqual(status2, "unchanged")
        self.assertEqual(diff2, 0)

        # Progressive update with 2 more messages
        sample_v2 = [
            {
                "uuid": "session-ext-1",
                "name": "Progressive Chat",
                "created_at": "2026-09-01T10:00:00Z",
                "chat_messages": [
                    {"uuid": "m1", "sender": "human", "text": "Hello 1"},
                    {"uuid": "m2", "sender": "assistant", "text": "Hi 1"},
                    {"uuid": "m3", "sender": "human", "text": "Hello 2"},
                    {"uuid": "m4", "sender": "assistant", "text": "Hi 2"},
                ],
            }
        ]
        s2 = detect_and_parse(sample_v2)[0]
        status3, diff3 = db.upsert_session(s2)
        self.assertEqual(status3, "updated")
        self.assertEqual(diff3, 2)

        retrieved = db.get_session(s2.session_id)
        self.assertEqual(len(retrieved.messages), 4)

    def test_in_memory_db_creates_no_file(self):
        cwd_memory_file = Path(":memory:")
        self.assertFalse(cwd_memory_file.exists())
        db = Database(":memory:")
        self.assertTrue(db.is_memory)
        self.assertFalse(cwd_memory_file.exists())

    def test_sanitize_fts5_query_edge_cases(self):
        from aigator.db import sanitize_fts5_query

        # Basic words
        self.assertEqual(sanitize_fts5_query("python rust"), '"python"* AND "rust"*')
        # Quoted phrase
        self.assertEqual(sanitize_fts5_query('"machine learning"'), '"machine learning"')
        # Special characters & symbols
        self.assertIn("fts5", sanitize_fts5_query("sqlite:fts5-test?"))
        # Empty string
        self.assertEqual(sanitize_fts5_query("   "), "")


if __name__ == "__main__":
    unittest.main()
