"""Parser for VS Code GitHub Copilot Chat session-store.db and export payloads."""

from __future__ import annotations

from contextlib import closing
import os
import sys
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from aigator.models import CanonicalMessage, CanonicalSession, Role, Source, format_iso, normalize_iso_datetime
from aigator.parsers.base import BaseParser, register_parser

if sys.platform == "darwin":
    _config_dir = Path.home() / "Library" / "Application Support"
elif sys.platform == "win32":
    _config_dir = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
else:
    _config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
COPILOT_GLOBAL_STORAGE = _config_dir / "Code" / "User" / "globalStorage" / "github.copilot-chat"
COPILOT_DB_DEFAULT = Path(os.environ.get("AIGATOR_COPILOT_DB", COPILOT_GLOBAL_STORAGE / "session-store.db"))


def clean_copilot_text(text: str) -> str:
    """Extract user prompt from potential Copilot XML envelope."""
    if not text:
        return ""
    m = re.search(r"<userRequest>(.*?)</userRequest>", text, flags=re.DOTALL)
    if m:
        return m.group(1).strip()
    cleaned = re.sub(r"<attachments>.*?</attachments>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<context>.*?</context>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<environment_info>.*?</environment_info>", "", cleaned, flags=re.DOTALL)
    return cleaned.strip() or text


def clean_copilot_title(raw_title: str, fallback_msg: str = "") -> str:
    """Derive clean single-line title for Copilot sessions."""
    candidate = clean_copilot_text(raw_title)
    if not candidate or candidate.startswith("<"):
        candidate = clean_copilot_text(fallback_msg)
    lines = [l.strip() for l in candidate.splitlines() if l.strip() and not l.strip().startswith("<")]
    title = lines[0] if lines else "Untitled Copilot Chat"
    if len(title) > 100:
        title = title[:97] + "..."
    return title or "Untitled Copilot Chat"


@register_parser
class VSCodeCopilotParser(BaseParser):
    name = "vscode_copilot"

    def can_parse(self, data: Any) -> bool:
        if isinstance(data, dict):
            if data.get("source") == "vscode_copilot" or "copilot_session_id" in data:
                return True
        elif isinstance(data, list) and len(data) > 0:
            sample = data[0]
            if isinstance(sample, dict) and (sample.get("source") == "vscode_copilot" or "copilot_session_id" in sample):
                return True
        return False

    def parse_db_file(self, db_path: Union[str, Path]) -> List[CanonicalSession]:
        """Read and parse all sessions directly from a SQLite session-store.db file."""
        path = Path(db_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Copilot session store not found: {path}")

        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.execute("BEGIN")
            conn.row_factory = sqlite3.Row

            session_rows = conn.execute("SELECT * FROM sessions ORDER BY created_at ASC").fetchall()
            sessions: List[CanonicalSession] = []

            for s in session_rows:
                sid = s["id"]
                title = (s["summary"] or "").strip()
                created_at = normalize_iso_datetime(s["created_at"])
                updated_at = normalize_iso_datetime(s["updated_at"])

                turns = conn.execute(
                    "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_index ASC", (sid,)
                ).fetchall()

                messages: List[CanonicalMessage] = []
                turn_counter = 0

                for t in turns:
                    t_time = normalize_iso_datetime(t["timestamp"]) or created_at
                    u_msg = (t["user_message"] or "").strip()
                    a_msg = (t["assistant_response"] or "").strip()

                    if u_msg:
                        messages.append(
                            CanonicalMessage(
                                id=f"copilot_{sid}_u{t['turn_index']}",
                                turn_index=turn_counter,
                                role=Role.USER.value,
                                content=u_msg,
                                timestamp=t_time,
                            )
                        )
                        turn_counter += 1

                    if a_msg:
                        messages.append(
                            CanonicalMessage(
                                id=f"copilot_{sid}_a{t['turn_index']}",
                                turn_index=turn_counter,
                                role=Role.ASSISTANT.value,
                                content=a_msg,
                                timestamp=t_time,
                            )
                        )
                        turn_counter += 1

                if not messages:
                    continue

                fallback_text = messages[0].content if messages else ""
                clean_title = clean_copilot_title(title, fallback_text)

                tags = ["vscode_copilot"]
                repo = s["repository"] if "repository" in s.keys() and s["repository"] else None
                if repo:
                    tags.append(repo)

                metadata = {
                    "cwd": s["cwd"] if "cwd" in s.keys() else None,
                    "repository": repo,
                    "branch": s["branch"] if "branch" in s.keys() else None,
                    "agent_name": s["agent_name"] if "agent_name" in s.keys() else None,
                }

                canonical_sess = CanonicalSession.create(
                    source=Source.VSCODE_COPILOT.value,
                    native_id=sid,
                    title=clean_title,
                    created_at=messages[0].timestamp if messages else created_at,
                    updated_at=updated_at,
                    tags=tags,
                    metadata=metadata,
                    messages=messages,
                )
                sessions.append(canonical_sess)

            return sessions

    def parse(self, data: Any, raw_payload: Optional[str] = None) -> List[CanonicalSession]:
        sessions: List[CanonicalSession] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    sess = CanonicalSession.from_dict(item)
                    sessions.append(sess)
        elif isinstance(data, dict):
            sess = CanonicalSession.from_dict(data)
            sessions.append(sess)
        return sessions
