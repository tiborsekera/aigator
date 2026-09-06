"""Parser for ChatGPT conversations.json exports and web payloads."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from aigator.models import CanonicalMessage, CanonicalSession, Role, Source, ToolCall, format_iso
from aigator.parsers.base import BaseParser, register_parser


def _timestamp_to_iso(ts: Any) -> str:
    if ts is None:
        return format_iso()
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except Exception:
            return format_iso()
    if isinstance(ts, str):
        return ts
    return format_iso()


def _extract_chatgpt_parts(parts: Any) -> str:
    """Extract and format textual content from ChatGPT parts list."""
    if not parts:
        return ""
    if isinstance(parts, str):
        return parts

    chunks: List[str] = []
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                if "text" in part:
                    chunks.append(str(part["text"]))
                elif part.get("content_type") == "image_asset_pointer":
                    chunks.append("[Image Attachment]")
                elif "asset_pointer" in part:
                    chunks.append("[Asset Attachment]")
                else:
                    chunks.append(str(part))
            else:
                chunks.append(str(part))

    return "\n".join(chunks).strip()


@register_parser
class ChatGPTParser(BaseParser):
    name = "chatgpt"

    def can_parse(self, data: Any) -> bool:
        if isinstance(data, list) and len(data) > 0:
            sample = data[0]
            if isinstance(sample, dict):
                if sample.get("source") == "chatgpt" or "mapping" in sample or ("title" in sample and "create_time" in sample):
                    return True
        elif isinstance(data, dict):
            if data.get("source") == "chatgpt" or "mapping" in data or ("conversation_id" in data and "title" in data):
                return True
        return False

    def _linearize_mapping(self, mapping: Dict[str, Any], current_node: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Extract valid messages from the mapping DAG in chronological order.
        """
        nodes = list(mapping.values())
        has_tree = any(isinstance(n, dict) and "parent" in n for n in nodes)
        if has_tree:
            if current_node not in mapping:
                parents = {n.get("parent") for n in nodes if isinstance(n, dict)}
                leaves = [k for k in mapping if k not in parents]
                current_node = max(leaves, key=lambda k: ((mapping[k].get("message") or {}).get("create_time") or 0, k), default=None)
            nodes = []
            seen = set()
            while current_node in mapping and current_node not in seen:
                seen.add(current_node)
                node = mapping[current_node]
                nodes.append(node)
                current_node = node.get("parent")
            nodes.reverse()
        valid_nodes: List[Dict[str, Any]] = []

        for node in nodes:
            if not isinstance(node, dict):
                continue
            msg = node.get("message")
            if not msg or not isinstance(msg, dict):
                continue
            
            author = msg.get("author", {})
            role = author.get("role", "system") if isinstance(author, dict) else "system"
            
            # Skip pure system prompts if empty or internal routing unless informative
            content_dict = msg.get("content", {})
            parts = content_dict.get("parts", []) if isinstance(content_dict, dict) else []
            text = _extract_chatgpt_parts(parts)
            
            metadata = msg.get("metadata")
            if not text and not (isinstance(metadata, dict) and metadata.get("aggregate_result")):
                continue

            valid_nodes.append(node)

        # Sort nodes by create_time
        def get_create_time(n: Dict[str, Any]) -> float:
            m = n.get("message") or {}
            ct = m.get("create_time")
            if isinstance(ct, (int, float)):
                return float(ct)
            return 0.0

        if not has_tree:
            valid_nodes.sort(key=get_create_time)
        return valid_nodes

    def _parse_single_conversation(
        self, conv: Dict[str, Any], raw_payload: Optional[str] = None
    ) -> Optional[CanonicalSession]:
        native_id = str(conv.get("id") or conv.get("conversation_id") or "")
        title = conv.get("title") or "Untitled ChatGPT Chat"
        create_time = _timestamp_to_iso(conv.get("create_time") or conv.get("created_at"))
        update_time = _timestamp_to_iso(conv.get("update_time") or conv.get("updated_at") or conv.get("create_time") or conv.get("created_at"))

        mapping = conv.get("mapping") or {}
        ordered_nodes = self._linearize_mapping(mapping, conv.get("current_node"))

        messages: List[CanonicalMessage] = []
        turn_idx = 0

        # Handle direct turns list (e.g. from browser extension or live capture)
        if "turns" in conv and isinstance(conv["turns"], list):
            for t in conv["turns"]:
                if not isinstance(t, dict):
                    continue
                author = str(t.get("author") or t.get("role") or "user").lower()
                norm_role = Role.USER.value if author in ("user", "human") else (
                    Role.ASSISTANT.value if author in ("assistant", "model", "bot") else Role.SYSTEM.value
                )
                text = str(t.get("text") or t.get("content") or "").strip()
                if not text:
                    continue
                msg_id = str(t.get("id") or f"turn_{turn_idx}")
                msg_ts = t.get("timestamp") or create_time
                messages.append(
                    CanonicalMessage(
                        id=msg_id,
                        turn_index=turn_idx,
                        role=norm_role,
                        content=text,
                        timestamp=msg_ts,
                    )
                )
                turn_idx += 1
        else:
            for node in ordered_nodes:
                msg = node.get("message", {})
                msg_id = str(msg.get("id") or node.get("id") or f"msg_{turn_idx}")
                author = msg.get("author") or {}
                raw_role = author.get("role", "user") if isinstance(author, dict) else "user"

                # Normalize role
                if raw_role in ("user", "human"):
                    norm_role = Role.USER.value
                elif raw_role in ("assistant", "model"):
                    norm_role = Role.ASSISTANT.value
                elif raw_role in ("tool", "executor"):
                    norm_role = Role.TOOL.value
                else:
                    norm_role = Role.SYSTEM.value

                content_dict = msg.get("content") or {}
                parts = content_dict.get("parts") if isinstance(content_dict, dict) else []
                content_text = _extract_chatgpt_parts(parts)

                metadata = msg.get("metadata") or {}
                model_slug = metadata.get("model_slug") if isinstance(metadata, dict) else None
                msg_ts = _timestamp_to_iso(msg.get("create_time"))

                # Tool calls / Code interpreter detections
                tool_calls: List[ToolCall] = []
                if raw_role == "tool" or "code_interpreter" in str(msg.get("recipient", "")):
                    tool_name = str(msg.get("recipient") or "code_interpreter")
                    tool_calls.append(
                        ToolCall(
                            tool_name=tool_name,
                            arguments={},
                            result=content_text,
                        )
                    )

                messages.append(
                    CanonicalMessage(
                        id=msg_id,
                        turn_index=turn_idx,
                        role=norm_role,
                        content=content_text,
                        timestamp=msg_ts,
                        model=model_slug,
                        tool_calls=tool_calls,
                        metadata=metadata if isinstance(metadata, dict) else {},
                    )
                )
                turn_idx += 1

        if not native_id:
            title_hash = hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]
            native_id = f"chatgpt_{title_hash}_{len(messages)}"

        return CanonicalSession.create(
            source=Source.CHATGPT.value,
            native_id=native_id,
            title=title,
            created_at=create_time,
            updated_at=update_time,
            tags=["chatgpt"],
            metadata={
                "model_slug": conv.get("default_model_slug"),
                "current_node": conv.get("current_node"),
                "branch_mapping": mapping,
                "moderation_results": conv.get("moderation_results", []),
            },
            messages=messages,
            raw_payload=raw_payload,
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
            # Check if it wraps conversations under a key
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
