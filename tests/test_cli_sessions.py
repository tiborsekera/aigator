import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aigator.parsers.cli_sessions import CliLiveWatcher, discover_cli_files, parse_cli_file


class CliSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, records, name="session.jsonl", tail=b""):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"".join(json.dumps(r).encode() + b"\n" for r in records) + tail)
        return path

    def codex(self):
        return [
            {"type": "session_meta", "timestamp": "2026-01-01T12:00:00Z", "payload": {
                "id": "codex-one", "base_instructions": "SECRET-INSTRUCTIONS", "cwd": "/private"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Find the bug"}},
            {"type": "response_item", "timestamp": "2026-01-01T12:01:00Z", "payload": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": "Find the bug"}]}},
            {"type": "response_item", "payload": {"type": "reasoning", "text": "SECRET-REASONING"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "channel": "analysis",
                "content": [{"type": "output_text", "text": "SECRET-ANALYSIS"}]}},
            {"type": "response_item", "payload": {"type": "function_call_output", "output": "SECRET-TOOL"}},
            {"type": "response_item", "timestamp": "2026-01-01T12:02:00Z", "payload": {
                "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Fixed the bug"}]}},
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "Fixed the bug"}},
        ]

    def test_codex_visible_messages_only_and_stable_ids(self):
        path = self.write(self.codex())
        session = parse_cli_file(path)[0]
        self.assertEqual(session.source, "codex")
        self.assertEqual([m.content for m in session.messages], ["Find the bug", "Fixed the bug"])
        self.assertNotIn("SECRET", json.dumps(session.to_dict()))
        self.assertEqual(session.to_dict(), parse_cli_file(path)[0].to_dict())
        self.assertEqual(session.updated_at, "2026-01-01T12:02:00")

    def test_claude_text_blocks_and_sidechains(self):
        base = {"sessionId": "claude-one", "timestamp": "2026-01-01T12:00:00Z"}
        records = [
            dict(base, type="user", uuid="u", message={"role": "user", "content": "Explain SQLite"}),
            dict(base, type="user", uuid="tool", message={"role": "user", "content": [
                {"type": "tool_result", "content": "SECRET-TOOL"}]}),
            dict(base, type="assistant", uuid="a", message={"role": "assistant", "content": [
                {"type": "thinking", "thinking": "SECRET-THINKING"}, {"type": "text", "text": "A local database"},
                {"type": "tool_use", "input": {"token": "SECRET"}}]}),
            dict(base, type="user", uuid="side", isSidechain=True, message={"role": "user", "content": "SECRET-SUBAGENT"}),
            dict(base, type="user", uuid="meta", isMeta=True, message={"role": "user", "content": "SECRET-META"}),
        ]
        records.append(records[2])
        session = parse_cli_file(self.write(records))[0]
        self.assertEqual(session.source, "claude_code")
        self.assertEqual([m.content for m in session.messages], ["Explain SQLite", "A local database"])
        self.assertNotIn("SECRET", json.dumps(session.to_dict()))

    def test_codex_fork_uses_last_session_metadata(self):
        records = self.codex() + [{"type": "session_meta", "payload": {
            "id": "fork-two", "timestamp": "2026-01-02T12:00:00Z"}}]
        session = parse_cli_file(self.write(records))[0]
        self.assertEqual(session.native_id, "fork-two")
        self.assertEqual(len(session.messages), 2)

    def test_codex_bootstrap_excluded_human_prompt_retained(self):
        wrapper = "# AGENTS.md instructions for /example\n\n<INSTRUCTIONS>PRIVATE RULES</INSTRUCTIONS>\n<environment_context>PRIVATE ENV</environment_context>\n"
        records = self.codex()
        records[2]["payload"]["content"] = [{"type": "input_text", "text": wrapper}]
        session = parse_cli_file(self.write(records))[0]
        self.assertEqual(len(session.messages), 1)
        self.assertEqual(session.title, "Coding session")
        records[2]["payload"]["content"][0]["text"] += "My actual question"
        session = parse_cli_file(self.write(records))[0]
        self.assertEqual(session.title, "My actual question")
        self.assertNotIn("PRIVATE", json.dumps(session.to_dict()))

    def test_malformed_shapes_and_timestamp_are_deterministic(self):
        records = self.codex()
        for record in records:
            record["timestamp"] = "not-a-date"
        session = parse_cli_file(self.write(records))[0]
        self.assertEqual(session.created_at, "1970-01-01T00:00:00")
        self.assertEqual(session.updated_at, "1970-01-01T00:00:00")
        with self.assertRaises(ValueError):
            parse_cli_file(self.write([{"type": []}]), "codex")
        records[2]["payload"]["role"] = []
        self.assertEqual(len(parse_cli_file(self.write(records))[0].messages), 1)

    def test_claude_same_message_id_separate_text_blocks_not_lost(self):
        records = [{"sessionId": "claude-one", "type": "assistant", "uuid": str(index),
                    "message": {"id": "shared", "role": "assistant", "content": [
                        {"type": "text", "text": text}]}}
                   for index, text in enumerate(["First block", "Second block"])]
        session = parse_cli_file(self.write(records))[0]
        self.assertEqual([m.content for m in session.messages], ["First block", "Second block"])

    def test_copilot_events(self):
        records = [
            {"type": "session.start", "data": {"sessionId": "copilot-one", "startTime": "2026-01-01T00:00:00Z"}},
            {"type": "user.message", "id": "u", "data": {"content": "Run tests", "transformedContent": "SECRET"}},
            {"type": "assistant.message", "id": "a", "data": {"content": "Tests passed", "reasoningText": "SECRET"}},
            {"type": "tool.execution_complete", "data": {"result": "SECRET"}},
        ]
        session = parse_cli_file(self.write(records))[0]
        self.assertEqual(session.source, "copilot_cli")
        self.assertEqual(len(session.messages), 2)
        self.assertNotIn("SECRET", json.dumps(session.to_dict()))

    def test_partial_tail_and_corruption(self):
        path = self.write(self.codex(), tail=b'{"type":')
        self.assertEqual(len(parse_cli_file(path)[0].messages), 2)
        path = self.write(self.codex(), tail=b'{"type":\n')
        with self.assertRaises(ValueError):
            parse_cli_file(path)
        with self.assertRaises(ValueError):
            parse_cli_file(self.write(self.codex()), "claude_code")
        with self.assertRaises(ValueError):
            parse_cli_file(self.write([{"unknown": True}]))

    def test_discovery_overrides_exclusions_and_archive(self):
        env = {"CLAUDE_CONFIG_DIR": str(self.root / "claude"), "CODEX_HOME": str(self.root / "codex"),
               "COPILOT_HOME": str(self.root / "copilot")}
        for name in ["claude/projects/project/main.jsonl", "claude/projects/project/subagents/agent-x.jsonl",
                     "claude/projects/project/agent-y.jsonl", "codex/sessions/2026/main.jsonl",
                     "codex/archived_sessions/archive.jsonl", "copilot/session-state/id/events.jsonl",
                     "copilot/session-state/id/other.jsonl"]:
            self.write([], name)
        with patch.dict(os.environ, env):
            found = list(discover_cli_files())
            self.assertEqual(len(found), 4)
            self.assertEqual(len(list(discover_cli_files("codex"))), 2)
        with self.assertRaises(ValueError):
            list(discover_cli_files("invalid"))

    def test_watcher_signature_and_retry(self):
        path = self.write(self.codex())
        class DB:
            calls = 0
            fail = False
            def upsert_session(self, session):
                self.calls += 1
                if self.fail:
                    raise ValueError("synthetic failure")
        db = DB()
        watcher = CliLiveWatcher(db)
        with patch("aigator.parsers.cli_sessions.discover_cli_files", return_value=[("codex", path)]):
            self.assertEqual(watcher.sync_once()["sessions"], 1)
            self.assertEqual(watcher.sync_once()["files"], 0)
            self.write(self.codex(), tail=b"\n")
            db.fail = True
            self.assertEqual(watcher.sync_once()["errors"], 1)
            db.fail = False
            self.assertEqual(watcher.sync_once()["sessions"], 1)


if __name__ == "__main__":
    unittest.main()
