#!/usr/bin/env python3
"""Seed an explicitly supplied, new database with fictional release-demo content.

Example: python scripts/demo_seed.py /tmp/aigator-demo/demo.db --serve
The optional demo server listens on 127.0.0.1:8767 and never watches local data.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aigator.db import Database
from aigator.models import CanonicalMessage, CanonicalSession


THREADS = [
    ("claude_code", "A quieter home for your AI conversations", [
        "Help me design a local-first conversation library. I want less clutter and a faster way to find useful ideas.",
        "Start with three things: a searchable conversation list, clear source filters, and a focused reading pane. Keep the archive on your device. Let the original AI apps do the chatting.",
        "What should the first screen prioritize?",
        "Make search the primary action. Show the conversation title, its source, and when it changed. In the reader, offer two clear actions: open the original conversation and copy the full thread. Every control should earn its place.",
    ]),
    ("codex", "Launch checklist for a small open-source tool", [
        "Give me a practical launch checklist for a local-first open-source app.",
        "Prepare a clear README, a short getting-started guide, and one screenshot with fictional data. Test a fresh install. Check the license and scan the repository for secrets. Then record a short demo that shows the everyday workflow.",
        "What should the demo show?",
        "Find a conversation, filter by provider, and copy a useful answer. Keep it under 20 seconds so the value is obvious without narration.",
    ]),
    ("gemini_web", "A tiny guide to keyboard shortcuts", [
        "Suggest a few memorable keyboard shortcuts for a conversation library.",
        "Use / to focus search, arrow keys to move through conversations, and Enter to open the selected result. Keep a visible hint beside the action so people can discover shortcuts as they work.",
        "How do we avoid getting in the way of normal text selection?",
        "Never override a shortcut while someone is typing or has selected text. Native copy should keep working. Add a separate, clearly labeled button for copying the whole conversation.",
    ]),
    ("vscode_copilot", "SQLite search without another service", [
        "How can I add full-text search to an app that already uses SQLite?",
        "Use SQLite FTS5 to index conversation titles and message content. Keep the index in the same database, update it when a session changes, and use snippets to show why each result matched.",
        "What is a good first performance check?",
        "Load a representative fictional dataset, search for common and rare terms, and measure latency. Add a source filter to verify that the results stay relevant as the archive grows.",
    ]),
    ("perplexity", "Notes on local-first software", [
        "What makes local-first software useful for a personal knowledge library?",
        "You control the archive, can search it without depending on a remote service, and can keep backups using tools you already trust. The best interfaces make that ownership feel simple in everyday use.",
    ]),
    ("chatgpt", "Naming a weekend project", [
        "I am building a small tool that gathers AI conversations. What should the name communicate?",
        "Choose something short, distinctive, and easy to say. A playful name can work well if the tagline is concrete: all your AI conversations, searchable in one place.",
    ]),
    ("copilot_cli", "Making a README easier to scan", [
        "How would you organize the README for a small developer tool?",
        "Lead with the problem it solves, then show the interface. Follow with installation, a first useful action, and a brief explanation of where data lives. Put advanced configuration later.",
    ]),
    ("claude_web", "A weekly review in fifteen minutes", [
        "Help me create a lightweight weekly review for my project notes.",
        "Spend five minutes finding the week's useful conversations, five minutes capturing decisions, and five minutes choosing the next small step. Keep a short list of open questions so good ideas do not disappear into the archive.",
    ]),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    path = args.database.expanduser().resolve()
    if path.exists():
        parser.error("Choose a new demo database path; existing databases are never modified.")
    db = Database(path)
    for index, (source, title, contents) in enumerate(THREADS):
        stamp = f"2026-09-07T{14-index:02d}:30:00"
        native_id = f"fictional-demo-{index + 1}"
        messages = [CanonicalMessage(
            id=f"{native_id}-{turn}", turn_index=turn,
            role="user" if turn % 2 == 0 else "assistant",
            content=content, timestamp=stamp,
        ) for turn, content in enumerate(contents)]
        db.upsert_session(CanonicalSession.create(
            source=source, native_id=native_id, title=title,
            created_at=stamp, updated_at=stamp, messages=messages,
            metadata={"synthetic_demo": True},
        ))
    if args.serve:
        # Set before importing the daemon: no real Copilot archive can be watched.
        os.environ["AIGATOR_COPILOT_DB"] = str(path.parent / "nonexistent-demo-copilot.db")
        from aigator.server.daemon import AigatorRequestHandler, LocalHTTPServer

        class DemoHandler(AigatorRequestHandler):
            def log_message(self, *_args):
                pass

        DemoHandler.db = db
        DemoHandler.auth_token = secrets.token_hex(24)
        with LocalHTTPServer(("127.0.0.1", args.port), DemoHandler) as server:
            server.serve_forever()
    else:
        print(f"Created {len(THREADS)} fictional conversations in {path}")


if __name__ == "__main__":
    main()
