"""Base parser interface and registry for AI conversation exports."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, Union

from aigator.models import CanonicalSession


class BaseParser(ABC):
    """Abstract base class for platform-specific session parsers."""

    name: str = "base"

    @abstractmethod
    def can_parse(self, data: Any) -> bool:
        """Return True if this parser can handle the given parsed JSON data or structure."""
        raise NotImplementedError

    @abstractmethod
    def parse(self, data: Any, raw_payload: Optional[str] = None) -> List[CanonicalSession]:
        """Parse raw data structure into a list of CanonicalSession objects."""
        raise NotImplementedError

    def parse_file(self, file_path: Union[str, Path]) -> List[CanonicalSession]:
        """Read and parse a file from disk."""
        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        raw_text = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw_text)
        return self.parse(data, raw_payload=raw_text)


_PARSER_REGISTRY: List[Type[BaseParser]] = []


def register_parser(cls: Type[BaseParser]) -> Type[BaseParser]:
    """Decorator to register a parser."""
    if cls not in _PARSER_REGISTRY:
        _PARSER_REGISTRY.append(cls)
    return cls


def get_registered_parsers() -> List[BaseParser]:
    """Instantiate and return all registered parsers."""
    return [cls() for cls in _PARSER_REGISTRY]


def detect_and_parse(
    input_data: Union[str, Path, Dict[str, Any], List[Any]],
    source_hint: Optional[str] = None,
) -> List[CanonicalSession]:
    """
    Automatically detect the format of input JSON/file/db and parse it into CanonicalSession objects.
    """
    raw_payload: Optional[str] = None
    data: Any = None

    if isinstance(input_data, (str, Path)):
        p = Path(input_data).expanduser()
        if p.exists() and p.is_file():
            if p.suffix.lower() == ".jsonl":
                from aigator.parsers.cli_sessions import parse_cli_file
                return parse_cli_file(p, source_hint=source_hint)
            # Check if SQLite DB file (e.g. VS Code Copilot session-store.db)
            if p.suffix == ".db" or "session-store" in p.name:
                from aigator.parsers.vscode_copilot import VSCodeCopilotParser
                return VSCodeCopilotParser().parse_db_file(p)

            raw_payload = p.read_text(encoding="utf-8", errors="replace")
            data = json.loads(raw_payload)
        elif isinstance(input_data, str):
            raw_payload = input_data
            data = json.loads(input_data)
    else:
        data = input_data
        raw_payload = json.dumps(data)

    parsers = get_registered_parsers()

    if source_hint:
        parsers = [parser for parser in parsers if parser.name == source_hint]
        if not parsers:
            raise ValueError(f"Unknown source parser: {source_hint}")
        if isinstance(data, dict) and data.get("source") not in (None, source_hint):
            raise ValueError("Payload source conflicts with requested parser")

    if isinstance(data, dict):
        for key in ("conversations", "threads", "chats"):
            if isinstance(data.get(key), list):
                return detect_and_parse(data[key], source_hint=source_hint)
    if isinstance(data, list) and source_hint and not parsers[0].can_parse(data):
        return [session for item in data
                for session in detect_and_parse(item, source_hint=source_hint)]

    for parser in parsers:
        turn_keys = {
            "chatgpt": ("turns",), "claude_web": ("turns",),
            "gemini_web": ("turns", "messages", "history"),
            "perplexity": ("turns", "messages"), "vscode_copilot": ("messages",),
        }
        explicit_turns = source_hint and isinstance(data, dict) and any(
            isinstance(data.get(key), list) for key in turn_keys.get(parser.name, ())
        )
        if parser.can_parse(data) or explicit_turns:
            sessions = parser.parse(data, raw_payload=raw_payload)
            if isinstance(data, dict) and data.get("capture_kind") == "dom":
                for session in sessions:
                    session.metadata["capture_kind"] = "dom"
                    session.metadata["captured_at"] = session.updated_at
            return sessions
    raise ValueError("Unrecognized data format. Could not find a suitable parser.")
