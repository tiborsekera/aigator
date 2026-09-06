"""Local CLI + HTTP integration using only synthetic files and isolated databases."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from aigator.cli import main
from aigator.db import Database
from aigator.parsers import detect_and_parse
from aigator.server.daemon import AigatorRequestHandler, LocalHTTPServer


class CliIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_path = self.root / "index.db"
        self.env = {"CLAUDE_CONFIG_DIR": str(self.root / "claude"),
                    "CODEX_HOME": str(self.root / "codex"),
                    "COPILOT_HOME": str(self.root / "copilot")}
        self.codex = self.root / "codex/sessions/2026/session.jsonl"
        self.codex.parent.mkdir(parents=True)
        records = [
            {"type": "session_meta", "payload": {"id": "synthetic-codex", "timestamp": "2026-01-01T00:00:00Z"}},
            {"type": "response_item", "timestamp": "2026-01-01T00:01:00Z", "payload": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": "SQLite search"}]}},
            {"type": "response_item", "timestamp": "2026-01-01T00:02:00Z", "payload": {
                "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Use a local index."}]}},
        ]
        self.codex.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    def run_cli(self, *args):
        output = io.StringIO()
        with patch.dict(os.environ, self.env), contextlib.redirect_stdout(output):
            main(["--db", str(self.db_path), *args])
        return output.getvalue()

    def test_main_source_sync_idempotence_and_missing_sources(self):
        self.assertIn("1 sessions", self.run_cli("sync", "--source", "codex"))
        self.assertIn("1 sessions", self.run_cli("sync", "--source", "codex"))
        self.assertIn("0 sessions", self.run_cli("sync", "--source", "claude_code"))
        db = Database(self.db_path)
        self.assertEqual(db.get_stats()["total_sessions"], 1)
        self.assertEqual(db.get_stats()["total_messages"], 2)
        self.assertEqual(db.list_sessions()[0]["source"], "codex")

    def test_main_all_sync_uses_only_overridden_roots(self):
        with patch("aigator.parsers.vscode_copilot.COPILOT_DB_DEFAULT", self.root / "missing.db"):
            self.assertIn("1 sessions", self.run_cli("sync"))
        self.assertEqual(Database(self.db_path).get_stats()["total_sessions"], 1)

    def test_jsonl_detection_and_main_import(self):
        session = detect_and_parse(self.codex)[0]
        self.assertEqual(session.source, "codex")
        self.assertEqual(session.native_id, "synthetic-codex")
        self.run_cli("import", str(self.codex))
        self.assertEqual(Database(self.db_path).get_stats()["total_sessions"], 1)
        with self.assertRaises(ValueError):
            detect_and_parse(self.codex, source_hint="claude_code")

    def test_main_sync_malformed_file_reports_failure(self):
        self.codex.write_text('{"type":\n', encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.run_cli("sync", "--source", "codex")
        self.assertEqual(Database(self.db_path).get_stats()["total_sessions"], 0)

    def test_http_search_fuzzy_and_keyword_modes(self):
        db = Database(self.db_path)
        for session in detect_and_parse(self.codex):
            db.upsert_session(session)
        class BoundHandler(AigatorRequestHandler):
            auth_token = "synthetic-integration-token"
        BoundHandler.db = db
        server = LocalHTTPServer(("127.0.0.1", 0), BoundHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def search(**params):
                request = Request(f"http://127.0.0.1:{server.server_port}/api/search?" + urlencode(params),
                                  headers={"Authorization": "Bearer synthetic-integration-token"})
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    return json.load(response)
            self.assertTrue(search(q="sql srch", fuzzy="1"))
            self.assertTrue(search(q="sql srch", fuzzy="true", source="codex"))
            self.assertEqual(search(q="sql srch", fuzzy="0"), [])
            self.assertEqual(search(q="sql srch"), [])
            self.assertTrue(search(q="SQLite", fuzzy="0"))
            self.assertEqual(search(q="sql srch", fuzzy="1", source="claude_code"), [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
