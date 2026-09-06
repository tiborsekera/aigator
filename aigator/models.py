"""Canonical data models for Aigator AI session aggregator."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.parse import quote


class Source(str, Enum):
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    COPILOT_CLI = "copilot_cli"
    CHATGPT = "chatgpt"
    CLAUDE_WEB = "claude_web"
    GEMINI_WEB = "gemini_web"
    PERPLEXITY = "perplexity"
    VSCODE_COPILOT = "vscode_copilot"
    HERMES_CLI = "hermes_cli"
    OTHER = "other"


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


def normalize_iso_datetime(val: Any = None) -> str:
    """
    Normalize to timezone-free UTC ISO text, preserving microseconds. Naive inputs mean UTC.
    """
    if val is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    if isinstance(val, (int, float)):
        # Handle milliseconds epoch
        if val > 1e11:
            val = val / 1000.0
        try:
            dt = datetime.fromtimestamp(val, tz=timezone.utc)
            return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat() if dt.tzinfo else dt.isoformat()
        except Exception:
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        # Handle ISO strings with Z or offsets
        clean_str = val_str.replace("Z", "+00:00").replace("z", "+00:00")
        try:
            dt = datetime.fromisoformat(clean_str)
            return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat() if dt.tzinfo else dt.isoformat()
        except Exception:
            pass

        # Regex fallback for YYYY-MM-DD with optional time
        m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}:\d{2}))?", val_str)
        if m:
            date_part = m.group(1)
            time_part = m.group(2) or "00:00:00"
            return f"{date_part}T{time_part}"

    if isinstance(val, datetime):
        return normalize_iso_datetime(val.isoformat())

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def format_iso(dt: Optional[datetime] = None) -> str:
    """Return standard YYYY-MM-DDTHH:MM:SS timestamp string."""
    return normalize_iso_datetime(dt)


def make_session_id(source: str, native_id: str) -> str:
    """Generate a deterministic, URL-safe session ID."""
    clean_native = str(native_id).strip()
    if not clean_native:
        clean_native = hashlib.sha256(format_iso().encode()).hexdigest()[:16]
    return f"{quote(str(source), safe='')}_{quote(clean_native, safe='')}"


@dataclass
class ToolCall:
    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    result: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ToolCall:
        return cls(
            tool_name=data.get("tool_name", ""),
            arguments=data.get("arguments", {}),
            result=data.get("result"),
        )


@dataclass
class CanonicalMessage:
    id: str
    turn_index: int
    role: str  # user, assistant, system, tool
    content: str
    timestamp: str = field(default_factory=format_iso)
    model: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.role not in {r.value for r in Role}:
            raise ValueError("Invalid message role")
        if not isinstance(self.content, str):
            raise ValueError("Message content must be text")
        self.timestamp = normalize_iso_datetime(self.timestamp)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "turn_index": self.turn_index,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "model": self.model,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CanonicalMessage:
        raw_tools = data.get("tool_calls", [])
        tool_calls = [
            ToolCall.from_dict(t) if isinstance(t, dict) else t for t in raw_tools
        ]
        return cls(
            id=data.get("id", ""),
            turn_index=data.get("turn_index", 0),
            role=data.get("role", Role.USER.value),
            content=data.get("content", ""),
            timestamp=normalize_iso_datetime(data.get("timestamp")),
            model=data.get("model"),
            tool_calls=tool_calls,
            metadata=data.get("metadata", {}),
        )


@dataclass
class CanonicalSession:
    session_id: str
    source: str
    native_id: str
    title: str
    created_at: str = field(default_factory=format_iso)
    updated_at: str = field(default_factory=format_iso)
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    messages: List[CanonicalMessage] = field(default_factory=list)
    raw_payload: Optional[str] = None

    def __post_init__(self) -> None:
        self.created_at = normalize_iso_datetime(self.created_at)
        self.updated_at = normalize_iso_datetime(self.updated_at)

    @classmethod
    def create(
        cls,
        source: str,
        native_id: str,
        title: str,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        messages: Optional[List[CanonicalMessage]] = None,
        raw_payload: Optional[str] = None,
    ) -> CanonicalSession:
        sid = make_session_id(source, native_id)
        now_str = format_iso()
        norm_msgs = messages or []
        for m in norm_msgs:
            m.timestamp = normalize_iso_datetime(m.timestamp)

        sess_created = normalize_iso_datetime(created_at) if created_at else (norm_msgs[0].timestamp if norm_msgs else now_str)
        # Preserve source modification time; use the last message only as a fallback.
        if updated_at:
            sess_updated = normalize_iso_datetime(updated_at)
        elif norm_msgs:
            sess_updated = norm_msgs[-1].timestamp
        else:
            sess_updated = normalize_iso_datetime(updated_at or created_at) if (updated_at or created_at) else now_str

        return cls(
            session_id=sid,
            source=source,
            native_id=native_id,
            title=title or "Untitled Session",
            created_at=sess_created,
            updated_at=sess_updated,
            tags=tags or [],
            metadata=metadata or {},
            messages=norm_msgs,
            raw_payload=raw_payload,
        )

    def to_dict(self, include_messages: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "session_id": self.session_id,
            "source": self.source,
            "native_id": self.native_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "tags": self.tags,
            "metadata": self.metadata,
            "message_count": len(self.messages),
        }
        if include_messages:
            d["messages"] = [m.to_dict() for m in self.messages]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CanonicalSession:
        raw_msgs = data.get("messages", [])
        messages = [
            CanonicalMessage.from_dict(m) if isinstance(m, dict) else m
            for m in raw_msgs
        ]
        created_at = normalize_iso_datetime(data.get("created_at"))
        updated_at = normalize_iso_datetime(data.get("updated_at"))
        if messages:
            if not data.get("updated_at"):
                updated_at = messages[-1].timestamp
            if not created_at:
                created_at = messages[0].timestamp

        return cls(
            session_id=data.get("session_id")
            or make_session_id(
                data.get("source", Source.OTHER.value),
                data.get("native_id", "unknown"),
            ),
            source=data.get("source", Source.OTHER.value),
            native_id=data.get("native_id", ""),
            title=data.get("title", "Untitled Session"),
            created_at=created_at,
            updated_at=updated_at,
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
            messages=messages,
            raw_payload=data.get("raw_payload"),
        )


@dataclass
class SearchResult:
    session_id: str
    message_id: str
    source: str
    title: str
    role: str
    snippet: str
    content: str
    timestamp: str
    session_updated_at: str
    score: float
    turn_index: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
