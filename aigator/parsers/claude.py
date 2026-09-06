"""Parser for Claude.ai conversations.json exports and web payloads."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from aigator.models import CanonicalMessage, CanonicalSession, Role, Source, ToolCall, format_iso
from aigator.parsers.base import BaseParser, register_parser


def _format_tool_use(name: str, inp: Any) -> str:
    """Render human-friendly inline description of Claude tool calls."""
    if name in ("web_search", "web_search_fast", "image_search") and isinstance(inp, dict):
        q = inp.get("query") or inp.get("q") or ""
        return f"🔍 *[Search: {q}]*" if q else f"*[Tool: {name}]*"
    elif name == "web_fetch" and isinstance(inp, dict):
        u = inp.get("url") or ""
        return f"🌐 *[Fetch: {u}]*" if u else f"*[Tool: {name}]*"
    elif name in ("bash_tool", "execute_code") and isinstance(inp, dict):
        cmd = inp.get("command") or ""
        return f"💻 *[Command: `{cmd}`]*" if cmd else f"*[Tool: {name}]*"
    elif isinstance(inp, dict) and "query" in inp:
        return f"*[Tool {name}: {inp['query']}]*"
    return f"*[Tool: {name}]*"


def _extract_claude_text(msg: Dict[str, Any]) -> str:
    """Extract text from Claude message whether stored in content blocks or text."""
    content = msg.get("content")
    blocks: List[str] = []
    if isinstance(content, list) and len(content) > 0:
        for b in content:
            if not isinstance(b, dict):
                continue
            b_type = b.get("type")
            if b_type == "text":
                t = (b.get("text") or "").strip()
                if t:
                    blocks.append(t)
            elif b_type == "tool_use":
                blocks.append(_format_tool_use(b.get("name", "tool"), b.get("input", {})))
            elif b_type == "tool_result":
                result = b.get("content", "")
                blocks.append(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False))

    # Fallback to direct text field with unsupported placeholder cleanup
    text = msg.get("text")
    if not blocks and isinstance(text, str) and text.strip():
        cleaned = re.sub(r'```\s*This block is not supported on your current device yet\.\s*```', '', text)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
        if cleaned:
            blocks.append(cleaned)

    # Attachments supplement the prompt, including when ordinary text exists.
    attachments = msg.get("attachments") or []
    if attachments and isinstance(attachments, list):
        att_texts = []
        for att in attachments:
            if isinstance(att, dict) and "extracted_content" in att:
                att_texts.append(f"[Attachment: {att.get('file_name', 'file')}]\n{att['extracted_content']}")
        blocks.extend(att_texts)

    return "\n\n".join(blocks).strip()


@register_parser
class ClaudeParser(BaseParser):
    name = "claude_web"

    def can_parse(self, data: Any) -> bool:
        if isinstance(data, list) and len(data) > 0:
            sample = data[0]
            if isinstance(sample, dict):
                if sample.get("source") == "claude_web" or "chat_messages" in sample or ("uuid" in sample and "name" in sample):
                    return True
        elif isinstance(data, dict):
            if data.get("source") == "claude_web" or "chat_messages" in data or ("uuid" in data and ("name" in data or "chat_messages" in data)):
                return True
        return False

    def _parse_single_conversation(
        self, conv: Dict[str, Any], raw_payload: Optional[str] = None
    ) -> Optional[CanonicalSession]:
        native_id = str(conv.get("uuid") or conv.get("id") or "")
        title = conv.get("name") or conv.get("title") or "Untitled Claude Chat"
        created_at = conv.get("created_at") or format_iso()
        updated_at = conv.get("updated_at") or created_at

        raw_messages = conv.get("chat_messages") or conv.get("turns") or []
        messages: List[CanonicalMessage] = []
        calls_by_id = {}

        for idx, m in enumerate(raw_messages):
            if not isinstance(m, dict):
                continue
            
            sender = m.get("sender") or m.get("author") or m.get("role") or "human"
            if sender in ("human", "user"):
                role = Role.USER.value
            elif sender in ("assistant", "claude", "model"):
                role = Role.ASSISTANT.value
            elif sender in ("tool", "tool_result"):
                role = Role.TOOL.value
            else:
                role = Role.SYSTEM.value

            text = _extract_claude_text(m)
            msg_id = str(m.get("uuid") or m.get("id") or f"claude_msg_{idx}")
            msg_ts = m.get("created_at") or m.get("timestamp") or created_at
            tool_calls = []
            for block in m.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    call = ToolCall(block.get("name") or "tool", block.get("input") or {})
                    tool_calls.append(call)
                    if block.get("id"):
                        calls_by_id[block["id"]] = call
                elif block.get("type") == "tool_result":
                    result = block.get("content", "")
                    result = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                    call = calls_by_id.get(block.get("tool_use_id"))
                    if call is not None:
                        call.result = result
                    else:
                        tool_calls.append(ToolCall("tool_result", {}, result))

            messages.append(
                CanonicalMessage(
                    id=msg_id,
                    turn_index=idx,
                    role=role,
                    content=text,
                    timestamp=msg_ts,
                    tool_calls=tool_calls,
                    metadata={
                        "files": m.get("files", []),
                        "attachments": m.get("attachments") or [],
                        "attachments_count": len(m.get("attachments") or []),
                    },
                )
            )

        if not native_id:
            title_hash = hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]
            native_id = f"claude_{title_hash}_{len(messages)}"

        return CanonicalSession.create(
            source=Source.CLAUDE_WEB.value,
            native_id=native_id,
            title=title,
            created_at=created_at,
            updated_at=updated_at,
            tags=["claude"],
            metadata={
                "account": conv.get("account", {}),
                "model": conv.get("model"),
            },
            messages=messages,
            raw_payload=raw_payload or json.dumps(conv),
        )

    def parse(self, data: Any, raw_payload: Optional[str] = None) -> List[CanonicalSession]:
        sessions: List[CanonicalSession] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    sess = self._parse_single_conversation(item)
                    if sess:
                        sessions.append(sess)
        elif isinstance(data, dict):
            if "conversations" in data and isinstance(data["conversations"], list):
                for item in data["conversations"]:
                    sess = self._parse_single_conversation(item)
                    if sess:
                        sessions.append(sess)
            else:
                sess = self._parse_single_conversation(data, raw_payload=raw_payload)
                if sess:
                    sessions.append(sess)

        return sessions
