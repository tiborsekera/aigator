"""Model Context Protocol (MCP) server for Aigator."""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional

from aigator.db import Database, get_default_db_path

SERVER_NAME = "aigator-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "aigator_search",
        "description": "Full-text search (BM25 keyword & phrase) across all indexed AI conversations (ChatGPT, Claude, Gemini, Perplexity, IDE sessions). Returns relevant message snippets with scores and timestamps.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search terms, phrase (in quotes), or keywords to search for.",
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional filter by platform (e.g. ['chatgpt', 'claude_web', 'gemini_web', 'perplexity']).",
                },
                "sort": {
                    "type": "string",
                    "enum": ["date", "rank"],
                    "description": "Sort by last message datetime ('date', default) or BM25 relevance ('rank').",
                    "default": "date",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 10).",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "aigator_get_session",
        "description": "Retrieve the complete multi-turn conversation thread, metadata, and tool traces for a specific session ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The unique session ID (e.g. 'chatgpt_xxx' or 'claude_web_xxx').",
                }
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "aigator_list_sessions",
        "description": "List the most recently updated AI sessions and their metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Optional source filter (e.g. 'chatgpt', 'claude_web', 'gemini_web', 'perplexity').",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max sessions to return (default 20).",
                    "default": 20,
                },
            },
        },
    },
    {
        "name": "aigator_stats",
        "description": "Get summary metrics on indexed sessions, message counts, and database storage.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


def handle_tool_call(db: Database, tool_name: str, arguments: Dict[str, Any]) -> Any:
    if tool_name == "aigator_search":
        query = arguments.get("query", "")
        sources = arguments.get("sources")
        sort_by = arguments.get("sort", "date")
        limit = int(arguments.get("limit", 10))
        results = db.search(query=query, sources=sources, limit=limit, sort_by=sort_by)
        if not results:
            return {"content": [{"type": "text", "text": f"No sessions matched query: {query}"}]}

        formatted = []
        for idx, r in enumerate(results, 1):
            clean_snippet = r.snippet.replace("«", "**").replace("»", "**")
            dt_line = f" | **Last Msg:** `{r.session_updated_at}`" if r.session_updated_at else ""
            formatted.append(
                f"### Result {idx}: [{r.source}] {r.title}\n"
                f"- **Session ID:** `{r.session_id}`\n"
                f"- **Role:** {r.role} | **Score:** {r.score:.3f}{dt_line}\n"
                f"- **Snippet:** {clean_snippet}\n"
            )
        return {"content": [{"type": "text", "text": "\n".join(formatted)}]}

    elif tool_name == "aigator_get_session":
        session_id = arguments.get("session_id", "")
        sess = db.get_session(session_id)
        if not sess:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Session not found: {session_id}"}],
            }

        lines = [
            f"# {sess.title}",
            f"- **Source:** {sess.source}",
            f"- **Session ID:** `{sess.session_id}`",
            f"- **Created:** {sess.created_at}",
            f"- **Updated:** {sess.updated_at}",
            f"- **Messages:** {len(sess.messages)}",
            "",
            "---",
            "",
        ]

        for m in sess.messages:
            lines.append(f"### {m.role.upper()} ({m.timestamp})")
            if m.model:
                lines.append(f"*Model: {m.model}*")
            lines.append(m.content)
            if m.tool_calls:
                for tc in m.tool_calls:
                    lines.append(f"```json\n[Tool: {tc.tool_name}]\nArgs: {json.dumps(tc.arguments)}\nResult: {tc.result}\n```")
            lines.append("\n---\n")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    elif tool_name == "aigator_list_sessions":
        source = arguments.get("source")
        limit = int(arguments.get("limit", 20))
        sessions = db.list_sessions(source=source, limit=limit)
        
        lines = [f"Found {len(sessions)} sessions:"]
        for s in sessions:
            lines.append(
                f"- **[{s['source']}]** {s['title']} (ID: `{s['session_id']}`, msgs: {s['message_count']}, updated: {s['updated_at'][:10]})"
            )
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    elif tool_name == "aigator_stats":
        stats = db.get_stats()
        return {"content": [{"type": "text", "text": json.dumps(stats, indent=2)}]}

    return {
        "isError": True,
        "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
    }


def run_mcp_server(
    db_path: Optional[str] = None, db: Optional[Database] = None
) -> None:
    """Run standard JSON-RPC MCP server over stdin/stdout."""
    active_db = db or Database(db_path or get_default_db_path())

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except Exception:
            continue

        if not isinstance(req, dict):
            continue

        msg_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        # In JSON-RPC 2.0, notifications do not have an id and must not receive a response
        if msg_id is None:
            continue

        if method == "initialize":
            client_version = params.get("protocolVersion") if isinstance(params, dict) else None
            negotiated_version = client_version if client_version in ("2024-11-05", "2025-03-26", "2025-06-18") else PROTOCOL_VERSION
            res = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": negotiated_version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            }
        elif method == "ping":
            res = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        elif method == "tools/list":
            res = {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        elif method == "tools/call":
            tool_name = params.get("name", "") if isinstance(params, dict) else ""
            arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
            try:
                call_res = handle_tool_call(active_db, tool_name, arguments)
                res = {"jsonrpc": "2.0", "id": msg_id, "result": call_res}
            except Exception as e:
                res = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"isError": True, "content": [{"type": "text", "text": str(e)}]},
                }
        else:
            res = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

        sys.stdout.write(json.dumps(res) + "\n")
        sys.stdout.flush()
