"""Command-line interface for Aigator session aggregator."""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from aigator.db import Database, get_default_db_path
from aigator.models import CanonicalSession
from aigator.parsers import detect_and_parse
from aigator.server.daemon import run_daemon
from aigator.server.mcp import run_mcp_server

# ANSI Color codes for clean terminal output
BOLD = "\033[1m"
GREEN = "\033[32m"
BLUE = "\033[34m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
DIM = "\033[2m"
RESET = "\033[0m"


def format_source_badge(source: str) -> str:
    badges = {
        "claude_code": f"{YELLOW}[Claude Code]{RESET}",
        "codex": f"{GREEN}[Codex]{RESET}",
        "copilot_cli": f"{MAGENTA}[Copilot CLI]{RESET}",
        "chatgpt": f"{GREEN}[ChatGPT]{RESET}",
        "claude_web": f"{YELLOW}[Claude]{RESET}",
        "gemini_web": f"{BLUE}[Gemini]{RESET}",
        "perplexity": f"{MAGENTA}[Perplexity]{RESET}",
        "vscode_copilot": f"{CYAN}[Copilot]{RESET}",
    }
    return badges.get(source, f"{CYAN}[{source}]{RESET}")


def import_zip_file(zip_path: Path, source_hint: Optional[str] = None) -> List[CanonicalSession]:
    """Inspect and extract conversations from a zip archive."""
    all_sessions: List[CanonicalSession] = []
    with zipfile.ZipFile(zip_path, "r") as z:
        members = [info for info in z.infolist() if info.filename.lower().endswith(".json")]
        if any(m.file_size > 50 * 1024 * 1024 for m in members) or sum(m.file_size for m in members) > 100 * 1024 * 1024:
            raise ValueError("ZIP JSON exceeds import limits (50 MB/member, 100 MB total)")
        for member in members:
            with z.open(member) as f:
                content = f.read(50 * 1024 * 1024 + 1)
                if len(content) > 50 * 1024 * 1024:
                    raise ValueError("ZIP member exceeds import limit")
                try:
                    sessions = detect_and_parse(json.loads(content), source_hint=source_hint)
                    all_sessions.extend(sessions)
                except ValueError as exc:
                    if str(exc).startswith("Unrecognized data format"):
                        continue  # Exports also contain unrelated account/settings JSON.
                    raise ValueError(f"Failed to parse {member.filename}: {exc}") from exc
    if not all_sessions:
        raise ValueError("No importable JSON conversations found; HTML-only ZIP exports are unsupported")
    return all_sessions


def _upsert_sessions_batch(db: Database, sessions: List[CanonicalSession], label: str) -> Dict[str, int]:
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "skipped_stale": 0, "new_msgs": 0}
    for s in sessions:
        status, diff = db.upsert_session(s)
        counts[status] += 1
        if status == "updated":
            counts["new_msgs"] += diff

    details = []
    if counts["inserted"]:
        details.append(f"{counts['inserted']} new")
    if counts["updated"]:
        details.append(f"{counts['updated']} updated (+{counts['new_msgs']} msgs)")
    if counts["unchanged"]:
        details.append(f"{counts['unchanged']} unchanged")
    if counts["skipped_stale"]:
        details.append(f"{counts['skipped_stale']} skipped (already has newer/more msgs)")

    detail_str = f" ({', '.join(details)})" if details else ""
    print(f"✅ Processed {len(sessions)} sessions from {label}{detail_str}")
    return counts


def cmd_import(args: argparse.Namespace, db: Database) -> None:
    from aigator.parsers.vscode_copilot import COPILOT_DB_DEFAULT, VSCodeCopilotParser

    failures = 0
    processed = 0
    total_inserted = 0
    total_updated = 0
    total_unchanged = 0

    if getattr(args, "copilot", False):
        if not COPILOT_DB_DEFAULT.exists():
            failures += 1
            print(f"❌ VS Code Copilot session store not found at {COPILOT_DB_DEFAULT}", file=sys.stderr)
        else:
            parser = VSCodeCopilotParser()
            sessions = parser.parse_db_file(COPILOT_DB_DEFAULT)
            res = _upsert_sessions_batch(db, sessions, "VS Code Copilot storage")
            processed += len(sessions)
            total_inserted += res["inserted"]
            total_updated += res["updated"]
            total_unchanged += res["unchanged"]

    patterns: List[str] = args.paths or []
    for pattern in patterns:
        expanded = glob.glob(os.path.expanduser(pattern), recursive=True)
        if not expanded:
            expanded = [pattern]

        for p_str in expanded:
            path = Path(p_str).expanduser()
            if not path.exists():
                failures += 1
                print(f"Warning: Path not found: {path}", file=sys.stderr)
                continue

            if path.is_file():
                if path.suffix.lower() == ".zip":
                    print(f"📦 Extracting zip archive: {path.name}...")
                    sessions = import_zip_file(path, source_hint=args.source)
                elif path.suffix.lower() == ".db" or "session-store" in path.name:
                    parser = VSCodeCopilotParser()
                    sessions = parser.parse_db_file(path)
                else:
                    try:
                        sessions = detect_and_parse(path, source_hint=args.source)
                    except Exception as e:
                        failures += 1
                        print(f"❌ Failed to parse {path.name}: {e}", file=sys.stderr)
                        continue

                res = _upsert_sessions_batch(db, sessions, path.name)
                processed += len(sessions)
                total_inserted += res["inserted"]
                total_updated += res["updated"]
                total_unchanged += res["unchanged"]

            elif path.is_dir():
                found_json_count = 0
                for json_file in (p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in (".json", ".jsonl")):
                    found_json_count += 1
                    try:
                        sessions = detect_and_parse(json_file, source_hint=args.source)
                        res = _upsert_sessions_batch(db, sessions, json_file.name)
                        processed += len(sessions)
                        total_inserted += res["inserted"]
                        total_updated += res["updated"]
                        total_unchanged += res["unchanged"]
                    except Exception as e:
                        failures += 1
                        sys.stderr.write(f"  ⚠️  Skipped {json_file.name}: {e}\n")
                        continue
                if found_json_count == 0:
                    failures += 1
                    sys.stderr.write(f"  ⚠️  No .json or .jsonl files found in directory {path}\n")

    if failures or not processed:
        raise SystemExit("Import incomplete: one or more inputs failed or no sessions were found")
    print(f"\n🎉 Finished import: {total_inserted} inserted, {total_updated} updated, {total_unchanged} unchanged into {db.db_path.name}")


def cmd_copilot_sync(args: argparse.Namespace, db: Database) -> None:
    """Sync VS Code Copilot sessions directly into Aigator."""
    from aigator.parsers.vscode_copilot import COPILOT_DB_DEFAULT, VSCodeCopilotParser

    db_path = Path(args.copilot_db).expanduser() if getattr(args, "copilot_db", None) else COPILOT_DB_DEFAULT
    if not db_path.exists():
        print(f"❌ VS Code Copilot session store not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    print(f"🔄 Syncing VS Code Copilot sessions from {db_path}...")
    parser = VSCodeCopilotParser()
    sessions = parser.parse_db_file(db_path)
    res = _upsert_sessions_batch(db, sessions, "VS Code Copilot (session-store.db)")
    print(f"🎉 Done: {res['inserted']} new sessions, {res['updated']} extended sessions.")


def cmd_sync(args: argparse.Namespace, db: Database) -> None:
    """One-shot local CLI and VS Code sync; missing installations are optional."""
    from aigator.parsers.cli_sessions import sync_cli_sessions
    from aigator.parsers.vscode_copilot import COPILOT_DB_DEFAULT

    source = args.source
    if args.copilot_db and source not in (None, "vscode_copilot"):
        raise SystemExit("--copilot-db requires --source vscode_copilot (or no source)")
    if source == "vscode_copilot" or args.copilot_db:
        cmd_copilot_sync(args, db)
        return
    if source is None and COPILOT_DB_DEFAULT.exists():
        cmd_copilot_sync(args, db)
    counts = sync_cli_sessions(db, source=source)
    print(f"CLI sync: {counts['files']} files, {counts['sessions']} sessions, {counts['errors']} errors")
    if counts["errors"]:
        raise SystemExit("Sync incomplete: some local session files could not be read")


def cmd_search(args: argparse.Namespace, db: Database) -> None:
    query = " ".join(args.query).strip()
    if not query:
        print("Please provide a search query.", file=sys.stderr)
        return

    sources = [args.source] if args.source else None
    results = db.search(
        query=query,
        sources=sources,
        limit=args.limit,
        sort_by=getattr(args, "sort", "date"),
        fuzzy=getattr(args, "fuzzy", False),
    )

    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
        return

    if not results:
        print(f"No results found for query: '{query}'")
        return

    print(f"\nFound {len(results)} matches for '{BOLD}{query}{RESET}':\n")
    for idx, r in enumerate(results, 1):
        badge = format_source_badge(r.source)
        snippet_colored = r.snippet.replace("«", f"{BOLD}{YELLOW}").replace("»", f"{RESET}")
        dt_str = f" {DIM}({r.session_updated_at}){RESET}" if r.session_updated_at else ""
        print(f"{BOLD}{idx}. {r.title}{RESET} {badge}{dt_str}")
        print(f"   {DIM}ID: {r.session_id} | Role: {r.role} | Score: {r.score:.3f}{RESET}")
        print(f"   {snippet_colored}")
        print()


def cmd_list(args: argparse.Namespace, db: Database) -> None:
    sessions = db.list_sessions(source=args.source, limit=args.limit, offset=args.offset)

    if args.json:
        print(json.dumps(sessions, indent=2))
        return

    if not sessions:
        print("No sessions found in database.")
        return

    print(f"\n{BOLD}Recent Sessions ({len(sessions)}){RESET}:\n")
    for s in sessions:
        badge = format_source_badge(s["source"])
        date_str = s["updated_at"]
        print(f"• {badge} {BOLD}{s['title'][:50]}{RESET} ({date_str})")
        print(f"   {DIM}ID: {s['session_id']} | Messages: {s['message_count']}{RESET}")
    print()


def cmd_show(args: argparse.Namespace, db: Database) -> None:
    session = db.get_session(args.session_id)
    if not session:
        print(f"Session not found: {args.session_id}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(session.to_dict(include_messages=True), indent=2))
        return

    badge = format_source_badge(session.source)
    print(f"\n{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}{session.title}{RESET} {badge}")
    print(f"{DIM}ID: {session.session_id} | Created: {session.created_at} | Last Message: {session.updated_at} | Messages: {len(session.messages)}{RESET}")
    print(f"{BOLD}{'=' * 70}{RESET}\n")

    for m in session.messages:
        role_label = f"{GREEN}[USER]{RESET}" if m.role == "user" else f"{CYAN}[{m.role.upper()}]{RESET}"
        model_str = f" ({m.model})" if m.model else ""
        print(f"{role_label}{model_str} {DIM}{m.timestamp}{RESET}")
        print(m.content)
        if m.tool_calls and not args.no_tools:
            for tc in m.tool_calls:
                print(f"{YELLOW}[Tool: {tc.tool_name}]{RESET} {json.dumps(tc.arguments)}")
                if tc.result:
                    print(f"{DIM}Result: {tc.result[:200]}...{RESET}")
        print(f"\n{DIM}{'-' * 40}{RESET}\n")


def cmd_delete(args: argparse.Namespace, db: Database) -> None:
    success = db.delete_session(args.session_id)
    if success:
        print(f"✅ Deleted session: {args.session_id}")
    else:
        print(f"❌ Session not found: {args.session_id}", file=sys.stderr)
        sys.exit(1)


def cmd_stats(args: argparse.Namespace, db: Database) -> None:
    stats = db.get_stats()
    if args.json:
        print(json.dumps(stats, indent=2))
        return

    print(f"\n🐊 {BOLD}Aigator Database Statistics{RESET}")
    print(f"• Location:         {stats['db_path']}")
    print(f"• Database Size:    {stats['size_mb']} MB ({stats['size_bytes']:,} bytes)")
    print(f"• Total Sessions:   {BOLD}{stats['total_sessions']:,}{RESET}")
    print(f"• Total Messages:   {BOLD}{stats['total_messages']:,}{RESET}")
    print("\nSessions by Source:")
    for src, count in sorted(stats["sessions_by_source"].items(), key=lambda x: -x[1]):
        print(f"  - {format_source_badge(src)}: {count:,}")
    print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aigator",
        description="Local-first multi-platform AI session aggregator and search engine.",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to SQLite database (default: ~/.local/share/aigator/aigator.db)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Import
    p_import = subparsers.add_parser("import", help="Import session export JSON, ZIP archives, or Copilot DB")
    p_import.add_argument("paths", nargs="*", help="File paths or glob patterns to import")
    p_import.add_argument("--copilot", action="store_true", help="Auto-import active VS Code Copilot sessions")
    p_import.add_argument("--source", type=str, help="Force source parser (claude_code, codex, copilot_cli, chatgpt, claude_web, gemini_web, perplexity, vscode_copilot)")

    # Copilot sync command
    p_copilot = subparsers.add_parser("copilot", help="Sync active VS Code Copilot chat sessions")
    p_copilot.add_argument("--copilot-db", type=str, default=None, help="Custom path to Copilot session-store.db")
    p_sync = subparsers.add_parser("sync", help="Sync local Claude Code, Codex, Copilot CLI and VS Code sessions")
    p_sync.add_argument("--source", choices=["claude_code", "codex", "copilot_cli", "vscode_copilot"], help="Sync just this local source")
    p_sync.add_argument("--copilot-db", default=None, help="Sync only this VS Code Copilot session-store.db (legacy sync compatibility)")

    # Search
    p_search = subparsers.add_parser("search", help="Full-text search conversations")
    p_search.add_argument("query", nargs="+", help="Keywords or \"phrase query\" to search")
    p_search.add_argument("--source", type=str, help="Filter by source")
    p_search.add_argument("--limit", type=int, default=15, help="Max results (default: 15)")
    p_search.add_argument(
        "--sort",
        choices=["date", "rank"],
        default="date",
        help="Sort results by last message datetime (date) or BM25 relevance score (rank). Default: date.",
    )
    p_search.add_argument("--json", action="store_true", help="Output raw JSON")
    p_search.add_argument("--fuzzy", action="store_true", help="Match abbreviated words, best matches first (overrides --sort)")

    # List
    p_list = subparsers.add_parser("list", help="List recent conversations")
    p_list.add_argument("--source", type=str, help="Filter by source")
    p_list.add_argument("--limit", type=int, default=20, help="Max items (default: 20)")
    p_list.add_argument("--offset", type=int, default=0, help="Pagination offset")
    p_list.add_argument("--json", action="store_true", help="Output raw JSON")

    # Show
    p_show = subparsers.add_parser("show", help="Display full conversation thread")
    p_show.add_argument("session_id", help="Session ID to display")
    p_show.add_argument("--json", action="store_true", help="Output full session JSON")
    p_show.add_argument("--no-tools", action="store_true", help="Hide tool calls")

    # Delete
    p_del = subparsers.add_parser("delete", help="Delete a session")
    p_del.add_argument("session_id", help="Session ID to delete")

    # Daemon / Serve
    p_serve = subparsers.add_parser("daemon", aliases=["serve"], help="Run local ingestion daemon and Web UI")
    p_serve.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    p_serve.add_argument(
        "--token",
        type=str,
        default=None,
        help="Custom auth token (default: reads from ~/.local/share/aigator/auth_token or AIGATOR_AUTH_TOKEN)",
    )

    # MCP
    subparsers.add_parser("mcp", help="Run Model Context Protocol stdio server")

    # Stats
    p_stats = subparsers.add_parser("stats", help="Show database metrics and statistics")
    p_stats.add_argument("--json", action="store_true", help="Output metrics as JSON")

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    # Redirected Windows streams may use a legacy encoding. Keep JSON ASCII-safe
    # and escape unrepresentable human-readable characters instead of crashing.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "mcp":
        run_mcp_server(db_path=args.db)
        return

    if args.command in ("daemon", "serve"):
        run_daemon(
            host=args.host,
            port=args.port,
            db_path=args.db,
            auth_token=args.token,
        )
        return

    db = Database(args.db or get_default_db_path())

    if args.command == "copilot":
        cmd_copilot_sync(args, db)
    elif args.command == "sync":
        cmd_sync(args, db)
    elif args.command == "import":
        cmd_import(args, db)
    elif args.command == "search":
        cmd_search(args, db)
    elif args.command == "list":
        cmd_list(args, db)
    elif args.command == "show":
        cmd_show(args, db)
    elif args.command == "delete":
        cmd_delete(args, db)
    elif args.command == "stats":
        cmd_stats(args, db)


if __name__ == "__main__":
    main()
