"""Read-only import of visible messages from local coding-agent transcripts.

These are vendor-internal formats: unknown events are ignored. Raw events,
system instructions, reasoning, tools and attachment metadata are not stored.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
from datetime import datetime, timezone

from aigator.models import CanonicalMessage, CanonicalSession

CLI_SOURCES = ("claude_code", "codex", "copilot_cli")
log = logging.getLogger(__name__)


def discover_cli_files(source=None):
    """Yield source/path pairs, without following symlinked files or directories."""
    if source is not None and source not in CLI_SOURCES:
        raise ValueError("Unknown CLI source")
    roots = {
        "claude_code": [Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"],
        "codex": [Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / name
                  for name in ("sessions", "archived_sessions")],
        "copilot_cli": [Path(os.environ.get("COPILOT_HOME", Path.home() / ".copilot")) / "session-state"],
    }
    for kind in ([source] if source else CLI_SOURCES):
        for root in roots[kind]:
            root = root.expanduser()
            if root.is_symlink():
                continue
            for directory, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink()
                                 and not (kind == "claude_code" and d == "subagents"))
                for name in sorted(files):
                    path = Path(directory) / name
                    if path.is_symlink() or not name.endswith(".jsonl"):
                        continue
                    if kind == "copilot_cli" and name != "events.jsonl":
                        continue
                    if kind == "claude_code" and name.startswith("agent-"):
                        continue
                    yield kind, path


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block["text"] for block in content
                         if isinstance(block, dict) and isinstance(block.get("type"), str) and block.get("type") in
                         {"text", "input_text", "output_text"} and isinstance(block.get("text"), str))
    return ""


def _records(path):
    """Stream records so large tool outputs are not retained in memory."""
    with path.open("rb") as stream:
        for raw in stream:
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                if not raw.endswith(b"\n"):
                    break
                raise ValueError("Malformed CLI transcript record") from None
            if not isinstance(event, dict):
                raise ValueError("CLI transcript records must be objects")
            if "type" in event and not isinstance(event["type"], str):
                raise ValueError("CLI transcript event type must be text")
            yield event


def _timestamp(value, fallback="1970-01-01T00:00:00"):
    """Reject malformed timestamps without replacing them with the current time."""
    try:
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value,
                                          timezone.utc).replace(tzinfo=None).isoformat()
        if isinstance(value, str):
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return (date.astimezone(timezone.utc).replace(tzinfo=None)
                    if date.tzinfo else date).isoformat()
    except (ValueError, OverflowError, OSError):
        pass
    return fallback


def _codex_user_text(content):
    text = _text(content)
    # CLI bootstrap blocks are represented as user messages, but are not prompts.
    # Strip only recognized anchored wrappers, preserving any following question.
    text = re.sub(r"\A\s*# AGENTS\.md instructions for [^\n]+\n\s*<INSTRUCTIONS>.*?</INSTRUCTIONS>\s*",
                  "", text, flags=re.S)
    text = re.sub(r"\A\s*<environment_context>.*?</environment_context>\s*", "", text, flags=re.S)
    return text


def parse_cli_file(path, source_hint=None):
    """Parse a snapshot; tolerate only an incomplete, unterminated final JSON line."""
    path = Path(path)
    if source_hint is not None and source_hint not in CLI_SOURCES:
        raise ValueError("Unknown CLI source")
    detected = set()
    for event in _records(path):
        if event.get("type") == "session_meta":
            detected.add("codex")
        elif event.get("type") == "session.start":
            detected.add("copilot_cli")
        elif event.get("type") in {"user", "assistant"} and "sessionId" in event and "message" in event:
            detected.add("claude_code")
    if len(detected) > 1 or (source_hint and detected and source_hint not in detected):
        raise ValueError("Conflicting CLI transcript source")
    source = source_hint or next(iter(detected), None)
    if source is None:
        raise ValueError("Unrecognized CLI transcript format")
    native_id = None
    created = None
    rows = []
    seen_ids = set()
    for event in _records(path):
        kind = event.get("type")
        stamp = event.get("timestamp")
        content = ""
        role = None
        event_id = event.get("uuid") or event.get("id")
        if source == "claude_code":
            if event.get("isSidechain") or event.get("isMeta"):
                continue
            if kind not in {"user", "assistant"}:
                continue
            candidate = event.get("sessionId")
            if candidate and native_id and candidate != native_id:
                raise ValueError("Multiple Claude sessions in one transcript")
            native_id = candidate or native_id
            message = event.get("message", {})
            if not isinstance(message, dict):
                continue
            role = message.get("role", kind)
            content = _text(message.get("content"))
        elif source == "codex":
            payload = event.get("payload", {})
            if not isinstance(payload, dict):
                continue
            if kind == "session_meta":
                candidate = payload.get("id")
                # Forked rollouts retain their parent's metadata/history before
                # appending the fork's own session_meta. The last ID owns the file.
                native_id = candidate or native_id
                created = payload.get("timestamp") or stamp
            # event_msg repeats visible response_item messages; never import both.
            if kind != "response_item" or payload.get("type") != "message":
                continue
            role = payload.get("role")
            if payload.get("channel") == "analysis":
                continue
            content = _text(payload.get("content"))
            if role == "user":
                content = _codex_user_text(payload.get("content"))
            event_id = payload.get("id")
        else:
            data = event.get("data", {})
            if not isinstance(data, dict):
                continue
            if kind == "session.start":
                candidate = data.get("sessionId")
                if native_id and candidate and candidate != native_id:
                    raise ValueError("Multiple Copilot sessions in one transcript")
                native_id = candidate or native_id
                created = data.get("startTime") or stamp
            if kind not in {"user.message", "assistant.message"}:
                continue
            role = kind.split(".")[0]
            content = _text(data.get("content"))
            event_id = data.get("messageId") or event_id
        if not isinstance(role, str) or role not in {"user", "assistant"} or not content.strip():
            continue
        if event_id and str(event_id) in seen_ids:
            continue
        if event_id:
            seen_ids.add(str(event_id))
        rows.append((role, content, stamp, event_id))
    if not rows:
        return []
    if not isinstance(native_id, str) or not native_id:
        raise ValueError("CLI transcript has no stable session identifier")
    # A constant fallback is stable across repeated reads, unlike datetime.now().
    fallback = _timestamp(created or next((r[2] for r in rows if r[2]), None))
    title = next((r[1].strip().splitlines()[0][:100] for r in rows if r[0] == "user"), "Coding session")
    stamps = [_timestamp(r[2], fallback) for r in rows]
    session = CanonicalSession.create(source, native_id, title,
                                      created_at=_timestamp(created, stamps[0]),
                                      updated_at=max(stamps + [_timestamp(created, stamps[0])]))
    for index, (role, content, stamp, event_id) in enumerate(rows):
        stable = str(event_id) if event_id else str(index)
        digest = hashlib.sha256(stable.encode()).hexdigest()[:24]
        session.messages.append(CanonicalMessage(id=f"{session.session_id}_{digest}",
                                turn_index=index, role=role, content=content,
                                timestamp=_timestamp(stamp, fallback)))
    return [session]


class CliLiveWatcher(threading.Thread):
    """Incremental file-signature watcher; failed/changing snapshots are retried."""
    def __init__(self, db, interval_sec=4.0):
        super().__init__(daemon=True, name="aigator-cli-watch")
        self.db = db
        self.interval_sec = interval_sec
        self._signatures = {}
        self._stop_event = threading.Event()

    @staticmethod
    def _signature(path):
        stat = path.stat()
        return stat.st_ino, stat.st_size, stat.st_mtime_ns

    def sync_once(self, source=None):
        stats = {"files": 0, "sessions": 0, "errors": 0}
        for kind, path in discover_cli_files(source):
            key = kind, str(path)
            try:
                before = self._signature(path)
                if self._signatures.get(key) == before:
                    continue
                sessions = parse_cli_file(path, kind)
                if self._signature(path) != before:
                    continue
                for session in sessions:
                    self.db.upsert_session(session)
                    stats["sessions"] += 1
                self._signatures[key] = before
                stats["files"] += 1
            except (OSError, ValueError, TypeError, KeyError) as exc:
                stats["errors"] += 1
                # Avoid logging transcript text or private absolute filenames.
                log.warning("Could not sync %s transcript (%s); will retry", kind, type(exc).__name__)
        return stats

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            try:
                self.sync_once()
            except Exception as exc:
                log.warning("CLI watcher failed (%s); will retry", type(exc).__name__)
            self._stop_event.wait(self.interval_sec)


def sync_cli_sessions(db, source=None):
    return CliLiveWatcher(db).sync_once(source)
