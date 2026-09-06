"""Parser for Google Gemini Takeout exports, activity logs, and web payloads."""

from __future__ import annotations

import hashlib
import html as html_lib
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

from aigator.models import CanonicalMessage, CanonicalSession, Role, Source, format_iso
from aigator.parsers.base import BaseParser, register_parser


def clean_html_to_markdown(html_content: str) -> str:
    """Convert HTML from Gemini Takeout responses into clean Markdown."""
    if not html_content:
        return ""
    text = html_content
    # Headers
    text = re.sub(r'<h[1-6][^>]*>(.*?)</h[1-6]>', r'\n\n### \1\n\n', text, flags=re.DOTALL)
    # Paragraphs and linebreaks
    text = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n\n', text, flags=re.DOTALL)
    text = re.sub(r'<br\s*/?>', '\n', text)
    # List items
    text = re.sub(r'<li[^>]*>(.*?)</li>', r'* \1\n', text, flags=re.DOTALL)
    # Formatting
    text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', text, flags=re.DOTALL)
    text = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', text, flags=re.DOTALL)
    text = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', text, flags=re.DOTALL)
    text = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', text, flags=re.DOTALL)
    text = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', text, flags=re.DOTALL)
    text = re.sub(r'<pre[^>]*>(.*?)</pre>', r'```\n\1\n```\n', text, flags=re.DOTALL)
    # Strip any remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    # Unescape HTML entities
    text = html_lib.unescape(text)
    # Normalize excess newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def clean_prompt_text(raw_title: str) -> str:
    """Remove Google Takeout 'Prompted ', 'Branched ', etc. prefixes and truncate to first line."""
    clean = raw_title.strip()
    prefixes = [
        "Prompted ",
        "Branched ",
        "Added chat from link: ",
        "Created Gemini Canvas titled ",
    ]
    for p in prefixes:
        if clean.startswith(p):
            clean = clean[len(p):].strip()
    return clean


def clean_prompt_title(raw_title: str) -> str:
    clean = clean_prompt_text(raw_title)
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    first_line = lines[0] if lines else "Untitled Gemini Chat"
    if len(first_line) > 100:
        first_line = first_line[:97] + "..."
    return first_line or "Untitled Gemini Chat"


def _extract_gemini_content(turn: Any) -> str:
    if isinstance(turn, str):
        return turn
    if not isinstance(turn, dict):
        return str(turn)

    # Check common fields
    if "text" in turn and isinstance(turn["text"], str):
        return turn["text"]
    if "content" in turn:
        c = turn["content"]
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "\n".join(str(x) for x in c)
    if "parts" in turn:
        parts = turn["parts"]
        if isinstance(parts, list):
            chunks = []
            for p in parts:
                if isinstance(p, str):
                    chunks.append(p)
                elif isinstance(p, dict) and "text" in p:
                    chunks.append(p["text"])
            return "\n".join(chunks)

    return ""


@register_parser
class GeminiParser(BaseParser):
    name = "gemini_web"

    def can_parse(self, data: Any) -> bool:
        if isinstance(data, list) and len(data) > 0:
            sample = data[0]
            if isinstance(sample, dict):
                # Google Takeout My Activity format
                if "safeHtmlItem" in sample or ("header" in sample and "Gemini" in str(sample.get("header", ""))):
                    return True
                if sample.get("source") == "gemini_web":
                    return True
        elif isinstance(data, dict):
            if data.get("source") == "gemini_web":
                return True
            if "candidates" in data or "contents" in data or "safeHtmlItem" in data:
                return True
        return False

    def _parse_takeout_activity(self, items: List[Dict[str, Any]]) -> List[CanonicalSession]:
        """
        Parse and group Google Takeout Gemini Apps 'My Activity.json' into multi-turn conversations.
        """
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

        for item in items:
            if not isinstance(item, dict):
                continue
            raw_title = item.get("title", "").strip()
            # Skip pure feedback/system logs without conversation content
            if not raw_title or raw_title.startswith("Cleared ") or raw_title == "Used Gemini Apps":
                continue

            # Extract conversation ID from details URL
            conv_url = None
            for d in item.get("details") or []:
                if not isinstance(d, dict):
                    continue
                u = d.get("url", "")
                if "gemini.google.com/app/" in u:
                    conv_url = u
                    break
            
            if conv_url:
                cid = conv_url.split("/")[-1].strip()
            else:
                cid = f"act_{item.get('time', '')}"

            grouped[cid].append(item)

        sessions: List[CanonicalSession] = []

        for cid, group_items in grouped.items():
            # Sort items chronologically
            group_items.sort(key=lambda x: x.get("time", ""))

            first_item = group_items[0]
            clean_title = clean_prompt_title(first_item.get("title", ""))

            messages: List[CanonicalMessage] = []
            turn_idx = 0

            for it in group_items:
                it_time = it.get("time") or format_iso()
                u_text = clean_prompt_text(it.get("title", ""))

                if u_text:
                    messages.append(
                        CanonicalMessage(
                            id=f"gemini_{cid}_m{turn_idx}",
                            turn_index=turn_idx,
                            role=Role.USER.value,
                            content=u_text,
                            timestamp=it_time,
                        )
                    )
                    turn_idx += 1

                # Extract response HTML if present
                html_items = it.get("safeHtmlItem", [])
                if html_items:
                    html_snippets = [x.get("html", "") for x in html_items if isinstance(x, dict)]
                    combined_html = "\n\n".join(html_snippets)
                    resp_md = clean_html_to_markdown(combined_html)
                    if resp_md:
                        messages.append(
                            CanonicalMessage(
                                id=f"gemini_{cid}_m{turn_idx}",
                                turn_index=turn_idx,
                                role=Role.ASSISTANT.value,
                                content=resp_md,
                                timestamp=it_time,
                            )
                        )
                        turn_idx += 1

            if not messages:
                continue

            session = CanonicalSession.create(
                source=Source.GEMINI_WEB.value,
                native_id=cid,
                title=clean_title,
                created_at=messages[0].timestamp,
                updated_at=messages[-1].timestamp,
                tags=["gemini"],
                metadata={"turn_count": len(group_items)},
                messages=messages,
            )
            sessions.append(session)

        return sessions

    def _parse_single_conversation(
        self, conv: Dict[str, Any], raw_payload: Optional[str] = None
    ) -> Optional[CanonicalSession]:
        # If single Takeout item:
        if "safeHtmlItem" in conv or ("header" in conv and "title" in conv):
            takeout_res = self._parse_takeout_activity([conv])
            return takeout_res[0] if takeout_res else None

        native_id = str(conv.get("id") or conv.get("conversation_id") or "")
        title = clean_prompt_title(conv.get("title") or "Untitled Gemini Chat")
        created_at = conv.get("created_at") or conv.get("timestamp") or format_iso()
        updated_at = conv.get("updated_at") or created_at

        turns = conv.get("turns") or conv.get("messages") or conv.get("history") or []
        messages: List[CanonicalMessage] = []

        for idx, t in enumerate(turns):
            if not isinstance(t, dict):
                continue
            
            author = str(t.get("author") or t.get("role") or t.get("sender") or "user").lower()
            if author in ("user", "human"):
                role = Role.USER.value
            elif author in ("model", "gemini", "assistant", "bard"):
                role = Role.ASSISTANT.value
            else:
                role = Role.SYSTEM.value

            text = _extract_gemini_content(t)

            msg_id = str(t.get("id") or f"gemini_msg_{idx}")
            msg_ts = t.get("timestamp") or created_at

            messages.append(
                CanonicalMessage(
                    id=msg_id,
                    turn_index=idx,
                    role=role,
                    content=text,
                    timestamp=msg_ts,
                    metadata=t.get("metadata", {}),
                )
            )

        if not native_id:
            title_hash = hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]
            native_id = f"gemini_{title_hash}_{len(messages)}"

        return CanonicalSession.create(
            source=Source.GEMINI_WEB.value,
            native_id=native_id,
            title=title,
            created_at=created_at,
            updated_at=updated_at,
            tags=["gemini"],
            metadata={"raw_keys": list(conv.keys())},
            messages=messages,
            raw_payload=raw_payload,
        )

    def parse(self, data: Any, raw_payload: Optional[str] = None) -> List[CanonicalSession]:
        if isinstance(data, list):
            # Check if this is a Google Takeout activity list
            if len(data) > 0 and isinstance(data[0], dict) and ("safeHtmlItem" in data[0] or "header" in data[0]):
                return self._parse_takeout_activity(data)

            sessions: List[CanonicalSession] = []
            for item in data:
                if isinstance(item, dict):
                    sess = self._parse_single_conversation(item)
                    if sess:
                        sessions.append(sess)
            return sessions

        elif isinstance(data, dict):
            if "chats" in data and isinstance(data["chats"], list):
                sessions = []
                for item in data["chats"]:
                    sess = self._parse_single_conversation(item)
                    if sess:
                        sessions.append(sess)
                return sessions
            else:
                sess = self._parse_single_conversation(data, raw_payload=raw_payload)
                return [sess] if sess else []

        return []
