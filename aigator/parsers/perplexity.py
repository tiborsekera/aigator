"""Parser for Perplexity threads, collections, and web payloads."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from aigator.models import CanonicalMessage, CanonicalSession, Role, Source, format_iso
from aigator.parsers.base import BaseParser, register_parser


@register_parser
class PerplexityParser(BaseParser):
    name = "perplexity"

    def can_parse(self, data: Any) -> bool:
        if isinstance(data, list) and len(data) > 0:
            sample = data[0]
            if isinstance(sample, dict) and ("thread_id" in sample or "default_model" in sample or "search_focus" in sample or "entries" in sample):
                return True
        elif isinstance(data, dict):
            if "thread_id" in data or "search_focus" in data or "entries" in data or "thread" in data or "perplexity" in str(data.get("source", "")).lower():
                return True
        return False

    def _parse_single_thread(
        self, thread: Dict[str, Any], raw_payload: Optional[str] = None
    ) -> Optional[CanonicalSession]:
        native_id = str(thread.get("thread_id") or thread.get("id") or thread.get("uuid") or "")
        title = thread.get("title") or thread.get("query") or "Untitled Perplexity Thread"
        created_at = thread.get("created_at") or format_iso()
        updated_at = thread.get("updated_at") or created_at

        entries = thread.get("turns") or thread.get("entries") or thread.get("messages") or []
        messages: List[CanonicalMessage] = []
        turn_idx = 0

        # If thread has direct query & answer format:
        if not entries and ("query" in thread or "text" in thread or "answer" in thread):
            if "query" in thread:
                messages.append(
                    CanonicalMessage(
                        id=f"{native_id}_q0",
                        turn_index=0,
                        role=Role.USER.value,
                        content=str(thread["query"]),
                        timestamp=created_at,
                    )
                )
                turn_idx = 1
            ans = thread.get("text") or thread.get("answer") or ""
            if ans:
                # Add citations if available
                citations = thread.get("sources") or thread.get("citations") or []
                if citations:
                    cit_str = "\n\nSources:\n" + "\n".join(
                        f"- [{s.get('name', 'Source')}]({s.get('url', '')})" if isinstance(s, dict) else f"- {s}"
                        for s in citations
                    )
                    ans += cit_str

                messages.append(
                    CanonicalMessage(
                        id=f"{native_id}_a0",
                        turn_index=turn_idx,
                        role=Role.ASSISTANT.value,
                        content=str(ans),
                        timestamp=updated_at,
                        metadata={"sources": citations},
                    )
                )
        else:
            for idx, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue

                q = entry.get("query") or entry.get("prompt")
                if q:
                    messages.append(
                        CanonicalMessage(
                            id=f"{native_id}_e{idx}_q",
                            turn_index=turn_idx,
                            role=Role.USER.value,
                            content=str(q),
                            timestamp=entry.get("timestamp") or created_at,
                        )
                    )
                    turn_idx += 1

                ans = entry.get("text") or entry.get("answer") or entry.get("content") or ""
                sources = entry.get("sources") or entry.get("web_search_results") or []
                if sources:
                    cit_str = "\n\nSources:\n" + "\n".join(
                        f"- [{s.get('name', 'Source')}]({s.get('url', '')})" if isinstance(s, dict) else f"- {s}"
                        for s in sources
                    )
                    ans += cit_str

                if ans:
                    messages.append(
                        CanonicalMessage(
                            id=f"{native_id}_e{idx}_a",
                            turn_index=turn_idx,
                            role=(Role.USER.value if str(entry.get("author") or entry.get("role")).lower() in ("user", "human") else Role.ASSISTANT.value),
                            content=str(ans),
                            timestamp=entry.get("timestamp") or updated_at,
                            metadata={"sources": sources},
                        )
                    )
                    turn_idx += 1

        if not native_id:
            title_hash = hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]
            native_id = f"pplx_{title_hash}_{len(messages)}"

        return CanonicalSession.create(
            source=Source.PERPLEXITY.value,
            native_id=native_id,
            title=title,
            created_at=created_at,
            updated_at=updated_at,
            tags=["perplexity"],
            metadata={"search_focus": thread.get("search_focus")},
            messages=messages,
            raw_payload=raw_payload,
        )

    def parse(self, data: Any, raw_payload: Optional[str] = None) -> List[CanonicalSession]:
        sessions: List[CanonicalSession] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    sess = self._parse_single_thread(item)
                    if sess:
                        sessions.append(sess)
        elif isinstance(data, dict):
            if "threads" in data and isinstance(data["threads"], list):
                for item in data["threads"]:
                    sess = self._parse_single_thread(item)
                    if sess:
                        sessions.append(sess)
            else:
                sess = self._parse_single_thread(data, raw_payload=raw_payload)
                if sess:
                    sessions.append(sess)

        return sessions
