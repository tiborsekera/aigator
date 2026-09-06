"""Integration tests for Aigator Model Context Protocol (MCP) server."""

import io
import json
import unittest
from unittest.mock import patch

from aigator.db import Database
from aigator.models import CanonicalMessage, CanonicalSession, Role, Source
from aigator.server.mcp import run_mcp_server


class TestMCPServer(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        sess = CanonicalSession.create(
            source=Source.CHATGPT.value,
            native_id="mcp-test-001",
            title="MCP Protocol Testing",
            messages=[
                CanonicalMessage(
                    id="m1",
                    turn_index=0,
                    role=Role.USER.value,
                    content="What is Model Context Protocol?",
                ),
                CanonicalMessage(
                    id="m2",
                    turn_index=1,
                    role=Role.ASSISTANT.value,
                    content="MCP is an open standard for AI agent context retrieval.",
                ),
            ],
        )
        self.db.upsert_session(sess)

    def _run_mcp_session(self, input_messages: list) -> list:
        input_data = "\n".join(json.dumps(m) for m in input_messages) + "\n"
        stdin = io.StringIO(input_data)
        stdout = io.StringIO()

        with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
            run_mcp_server(db=self.db)

        output_lines = [l.strip() for l in stdout.getvalue().splitlines() if l.strip()]
        return [json.loads(l) for l in output_lines]

    def test_mcp_handshake_and_tools(self):
        client_requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            # Notifications must not receive a response
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "aigator_search", "arguments": {"query": "standard"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "aigator_stats", "arguments": {}},
            },
        ]

        responses = self._run_mcp_session(client_requests)
        # Should receive exactly 3 responses (requests 1, 2, 3, 4 minus notification) -> 4 responses
        self.assertEqual(len(responses), 4)

        # 1. Initialize response
        init_res = responses[0]
        self.assertEqual(init_res["id"], 1)
        self.assertEqual(init_res["result"]["protocolVersion"], "2024-11-05")

        # 2. Tools list
        tools_res = responses[1]
        self.assertEqual(tools_res["id"], 2)
        tool_names = [t["name"] for t in tools_res["result"]["tools"]]
        self.assertIn("aigator_search", tool_names)
        self.assertIn("aigator_get_session", tool_names)

        # 3. Search tool call
        search_res = responses[2]
        self.assertEqual(search_res["id"], 3)
        self.assertIn("MCP Protocol Testing", search_res["result"]["content"][0]["text"])

        # 4. Stats tool call
        stats_res = responses[3]
        self.assertEqual(stats_res["id"], 4)
        self.assertIn("total_sessions", stats_res["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
