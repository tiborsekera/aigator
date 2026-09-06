"""SQLite + FTS5 database management and search engine for Aigator."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import os
import re
import sqlite3
import sys
import uuid
import heapq
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aigator.models import (
    CanonicalMessage,
    CanonicalSession,
    SearchResult,
    ToolCall,
    format_iso,
)
from aigator.fuzzy import folded, tokens_for, like_pattern, score_match, result_snippet

DEFAULT_DB_DIR = Path.home() / ".local" / "share" / "aigator"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "aigator.db"


def get_default_db_path() -> Path:
    env_path = os.environ.get("AIGATOR_DB_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return DEFAULT_DB_PATH


def sanitize_fts5_query(query: str) -> str:
    """
    Sanitize raw user search string into valid SQLite FTS5 query syntax.
    Supports basic words, phrases in quotes, and prefix search.
    """
    cleaned = query.strip()
    if not cleaned:
        return ""

    # If user provided explicit double quotes, keep quoted phrases intact
    tokens: List[str] = []
    # Match quoted strings or single words
    parts = re.findall(r'"([^"]*)"|(\S+)', cleaned)
    for phrase, word in parts:
        if phrase:
            # Escape internal double quotes
            safe_phrase = phrase.replace('"', '""').strip()
            if safe_phrase:
                tokens.append(f'"{safe_phrase}"')
        elif word:
            # Strip punctuation that might break FTS5 unless it's a wildcard
            safe_word = re.sub(r'[^\w\*\-]', '', word).strip()
            if safe_word:
                # Add prefix match if word doesn't already have wildcard
                if not safe_word.endswith("*"):
                    tokens.append(f'"{safe_word}"*')
                else:
                    tokens.append(f'{safe_word}')

    if not tokens:
        return ""
    return " AND ".join(tokens)


class Database:
    """Manages SQLite storage and FTS5 full-text indexing for AI sessions."""

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self.is_memory = False
        self._anchor_conn: Optional[sqlite3.Connection] = None
        self._mem_uri: Optional[str] = None

        if db_path is None:
            self.db_path = get_default_db_path()
            self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        else:
            db_str = str(db_path).strip()
            if db_str == ":memory:" or (db_str.startswith("file:") and "mode=memory" in db_str):
                self.is_memory = True
                self.db_path = Path(":memory:")
                if db_str.startswith("file:"):
                    self._mem_uri = db_str
                else:
                    self._mem_uri = f"file:aigator_mem_{uuid.uuid4().hex}?mode=memory&cache=shared"
                # Hold anchor connection open so database persists while Database instance lives
                self._anchor_conn = sqlite3.connect(self._mem_uri, uri=True)
                self._anchor_conn.row_factory = sqlite3.Row
            else:
                self.db_path = Path(db_path).expanduser().resolve()
                self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        if not self.is_memory:
            fd = os.open(self.db_path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
            if os.name == "posix":
                for path in (self.db_path, Path(str(self.db_path) + "-wal"), Path(str(self.db_path) + "-shm")):
                    if path.exists():
                        path.chmod(0o600)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        if self.is_memory and self._mem_uri:
            conn = sqlite3.connect(self._mem_uri, uri=True, timeout=30.0)
        else:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create tables, indexes, and FTS5 virtual tables, guarded by PRAGMA user_version."""
        with self._get_connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                native_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                tags TEXT,
                metadata TEXT,
                message_count INTEGER NOT NULL DEFAULT 0,
                raw_payload TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_source ON sessions(source);
            CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_sessions_native_id ON sessions(source, native_id);

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                turn_index INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                model TEXT,
                timestamp TEXT NOT NULL,
                tool_calls TEXT,
                metadata TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session_turn ON messages(session_id, turn_index ASC);

            CREATE TABLE IF NOT EXISTS fuzzy_index (
                message_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_fuzzy_session ON fuzzy_index(session_id);

            CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
                session_id UNINDEXED,
                message_id UNINDEXED,
                source,
                title,
                role,
                content,
                tokenize = 'unicode61 remove_diacritics 2'
            );
            """)

            # Run normalization migration once
            user_ver = conn.execute("PRAGMA user_version").fetchone()[0]
            if user_ver < 1:
                self._normalize_existing_timestamps(conn)
                conn.execute("PRAGMA user_version = 1")
                conn.commit()

            # Transactional, idempotent backfill for existing databases.
            # Normalization is paid at ingestion, not typing.
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM fuzzy_index WHERE message_id NOT IN (SELECT id FROM messages)')
            rows = conn.execute('''SELECT m.id, m.session_id, s.title, m.content
                FROM messages m JOIN sessions s ON s.id=m.session_id
                LEFT JOIN fuzzy_index f ON f.message_id=m.id
                WHERE f.message_id IS NULL''')
            for row in rows:
                conn.execute('INSERT OR IGNORE INTO fuzzy_index VALUES (?, ?, ?)',
                             (row['id'], row['session_id'], folded(row['title'] + '\n' + row['content'])))
            conn.commit()

    def _normalize_existing_timestamps(self, conn: sqlite3.Connection) -> None:
        """
        Migrate/normalize all existing session and message timestamps to standard
        'YYYY-MM-DDTHH:MM:SS' and ensure sessions.updated_at is the last message datetime.
        """
        from aigator.models import normalize_iso_datetime

        # Fetch sessions
        sessions = conn.execute("SELECT id, created_at, updated_at FROM sessions").fetchall()
        for s in sessions:
            sid = s["id"]
            # Get latest message timestamp for this session
            msg_rows = conn.execute(
                "SELECT id, timestamp FROM messages WHERE session_id = ? ORDER BY turn_index ASC",
                (sid,),
            ).fetchall()

            if msg_rows:
                # Normalize each message timestamp
                last_msg_ts = None
                for mr in msg_rows:
                    norm_m_ts = normalize_iso_datetime(mr["timestamp"])
                    if norm_m_ts != mr["timestamp"]:
                        conn.execute("UPDATE messages SET timestamp = ? WHERE id = ?", (norm_m_ts, mr["id"]))
                    last_msg_ts = norm_m_ts

                norm_created = normalize_iso_datetime(msg_rows[0]["timestamp"])
                norm_updated = last_msg_ts or norm_created

                if norm_created != s["created_at"] or norm_updated != s["updated_at"]:
                    conn.execute(
                        "UPDATE sessions SET created_at = ?, updated_at = ? WHERE id = ?",
                        (norm_created, norm_updated, sid),
                    )
            else:
                norm_c = normalize_iso_datetime(s["created_at"])
                norm_u = normalize_iso_datetime(s["updated_at"])
                if norm_c != s["created_at"] or norm_u != s["updated_at"]:
                    conn.execute(
                        "UPDATE sessions SET created_at = ?, updated_at = ? WHERE id = ?",
                        (norm_c, norm_u, sid),
                    )
        conn.commit()

    def upsert_session(self, session: CanonicalSession) -> Tuple[str, int]:
        """
        Smart insert/update:
        - If session is new: inserts it and returns ('inserted', message_count).
        - Identical canonical content returns ('unchanged', 0).
        - Older, shorter, or lossy DOM-over-export snapshots return ('skipped_stale', 0).
        - Other edits update content while retaining available optional metadata.
        """
        from aigator.models import normalize_iso_datetime

        supplied_session = session
        session = copy.deepcopy(session)
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id, title, created_at, updated_at, message_count FROM sessions WHERE id = ?",
                (session.session_id,),
            ).fetchone()

            # Reuse pre-encoding IDs so existing imports do not duplicate on upgrade.
            if not existing and session.native_id:
                existing = conn.execute(
                    "SELECT id, title, created_at, updated_at, message_count FROM sessions WHERE source = ? AND native_id = ? LIMIT 1",
                    (session.source, session.native_id),
                ).fetchone()
                if existing:
                    session.session_id = existing["id"]
                    supplied_session.session_id = existing["id"]

            created_at = normalize_iso_datetime(session.created_at)
            updated_at = normalize_iso_datetime(session.updated_at or session.created_at)

            incoming_count = len(session.messages)

            if existing:
                exist_count = existing["message_count"]
                exist_updated = existing["updated_at"]

                stored = self.get_session(session.session_id)
                incoming_capture = session.metadata.get("capture_kind") == "dom"
                stored_capture = stored.metadata.get("capture_kind") == "dom"

                def is_prefix(shorter, longer):
                    return len(shorter) <= len(longer) and all(
                        a.role == b.role and a.content == b.content
                        for a, b in zip(shorter, longer)
                    )

                if incoming_capture and not stored_capture:
                    # Append only when the complete indexed thread matches. Never
                    # replace export text, timestamps, tools or attachments with DOM.
                    if not is_prefix(stored.messages, session.messages):
                        return ("skipped_stale", 0)
                    if incoming_count == exist_count:
                        return ("unchanged", 0)
                    if updated_at < exist_updated:
                        return ("skipped_stale", 0)
                    suffix = session.messages[exist_count:]
                    captured_at = session.metadata.get("captured_at", updated_at)
                    session = copy.deepcopy(stored)
                    session.metadata.setdefault("export_updated_at", stored.updated_at)
                    session.metadata["captured_at"] = captured_at
                    for idx, message in enumerate(suffix, exist_count):
                        message.id = f"capture_{idx}"
                        message.turn_index = idx
                        message.metadata["capture_kind"] = "dom"
                    session.messages.extend(suffix)
                    session.updated_at = updated_at
                    created_at = session.created_at
                    incoming_capture = False
                elif not incoming_capture and (stored_capture or "export_updated_at" in stored.metadata):
                    # Source modification time and DOM observation time are not
                    # comparable. Check only the previous export's timestamp.
                    export_time = stored.metadata.get("export_updated_at")
                    if export_time and updated_at < export_time:
                        return ("skipped_stale", 0)
                    if incoming_count < exist_count:
                        if not is_prefix(session.messages, stored.messages):
                            return ("skipped_stale", 0)
                        session.metadata["export_updated_at"] = updated_at
                        session.messages.extend(copy.deepcopy(stored.messages[incoming_count:]))
                        updated_at = session.updated_at = max(updated_at, stored.updated_at)
                        incoming_count = len(session.messages)
                    else:
                        session.metadata["export_updated_at"] = updated_at
                elif incoming_count < exist_count or updated_at < exist_updated:
                    return ("skipped_stale", 0)
                def comparable(s):
                    data = s.to_dict()
                    for m in data["messages"]:
                        m.pop("id", None)
                    return data
                # Keep optional export details when a newer snapshot omits them.
                session.metadata = {**stored.metadata, **session.metadata}
                if not incoming_capture:
                    session.metadata.pop("capture_kind", None)
                session.tags = list(dict.fromkeys(stored.tags + session.tags))
                session.raw_payload = session.raw_payload or stored.raw_payload
                for old, new in zip(stored.messages, session.messages):
                    if old.role == new.role and old.content == new.content:
                        new.metadata = {**old.metadata, **new.metadata}
                        new.tool_calls = new.tool_calls or old.tool_calls
                        new.model = new.model or old.model

                if comparable(stored) == comparable(session):
                    return ("unchanged", 0)

                status = "updated"
                diff = incoming_count - exist_count
            else:
                status = "inserted"
                diff = incoming_count

            # Delete old search index entries and messages if session exists
            conn.execute("DELETE FROM search_index WHERE session_id = ?", (session.session_id,))
            conn.execute("DELETE FROM fuzzy_index WHERE session_id = ?", (session.session_id,))
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session.session_id,))

            tags_json = json.dumps(session.tags)
            meta_json = json.dumps(session.metadata)

            conn.execute(
                """
                INSERT INTO sessions (
                    id, source, native_id, title, created_at, updated_at,
                    tags, metadata, message_count, raw_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source = excluded.source,
                    native_id = excluded.native_id,
                    title = excluded.title,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    tags = excluded.tags,
                    metadata = excluded.metadata,
                    message_count = excluded.message_count,
                    raw_payload = excluded.raw_payload
                """,
                (
                    session.session_id,
                    session.source,
                    session.native_id,
                    session.title,
                    created_at,
                    updated_at,
                    tags_json,
                    meta_json,
                    len(session.messages),
                    session.raw_payload,
                ),
            )

            # Insert messages & FTS index entries
            for idx, msg in enumerate(session.messages):
                msg_tools = json.dumps([t.to_dict() for t in msg.tool_calls])
                msg_meta = json.dumps(msg.metadata)
                raw_id = msg.id or f"m_{idx}"
                # Ensure global uniqueness across sessions
                msg_id = f"{session.session_id}:{raw_id}" if not raw_id.startswith(f"{session.session_id}:") else raw_id
                msg_ts = normalize_iso_datetime(msg.timestamp)
                conn.execute('INSERT INTO fuzzy_index VALUES (?, ?, ?)',
                             (msg_id, session.session_id, folded(session.title + '\n' + msg.content)))

                conn.execute(
                    """
                    INSERT INTO messages (
                        id, session_id, turn_index, role, content,
                        model, timestamp, tool_calls, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        msg_id,
                        session.session_id,
                        msg.turn_index if msg.turn_index is not None else idx,
                        msg.role,
                        msg.content,
                        msg.model,
                        msg_ts,
                        msg_tools,
                        msg_meta,
                    ),
                )

                # Index in FTS5
                conn.execute(
                    """
                    INSERT INTO search_index (
                        session_id, message_id, source, title, role, content
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.session_id,
                        msg_id,
                        session.source,
                        session.title,
                        msg.role,
                        msg.content,
                    ),
                )
            conn.commit()
            return (status, diff)

    def get_session(self, session_id: str) -> Optional[CanonicalSession]:
        """Fetch complete session with all messages in chronological order."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not row:
                return None

            msg_rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY turn_index ASC, timestamp ASC",
                (session_id,),
            ).fetchall()

            messages: List[CanonicalMessage] = []
            for mr in msg_rows:
                tools_data = json.loads(mr["tool_calls"]) if mr["tool_calls"] else []
                tool_calls = [ToolCall.from_dict(td) for td in tools_data]
                meta = json.loads(mr["metadata"]) if mr["metadata"] else {}
                messages.append(
                    CanonicalMessage(
                        id=mr["id"],
                        turn_index=mr["turn_index"],
                        role=mr["role"],
                        content=mr["content"],
                        model=mr["model"],
                        timestamp=mr["timestamp"],
                        tool_calls=tool_calls,
                        metadata=meta,
                    )
                )

            tags = json.loads(row["tags"]) if row["tags"] else []
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}

            return CanonicalSession(
                session_id=row["id"],
                source=row["source"],
                native_id=row["native_id"],
                title=row["title"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                tags=tags,
                metadata=metadata,
                messages=messages,
                raw_payload=row["raw_payload"],
            )

    def list_sessions(
        self,
        source: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List sessions metadata sorted by updated_at descending."""
        with self._get_connection() as conn:
            if source:
                rows = conn.execute(
                    """
                    SELECT id, source, native_id, title, created_at, updated_at,
                           tags, message_count
                    FROM sessions
                    WHERE source = ?
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (source, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, source, native_id, title, created_at, updated_at,
                           tags, message_count
                    FROM sessions
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()

            results = []
            for r in rows:
                tags = json.loads(r["tags"]) if r["tags"] else []
                results.append(
                    {
                        "session_id": r["id"],
                        "source": r["source"],
                        "native_id": r["native_id"],
                        "title": r["title"],
                        "created_at": r["created_at"],
                        "updated_at": r["updated_at"],
                        "tags": tags,
                        "message_count": r["message_count"],
                    }
                )
            return results

    def search(
        self,
        query: str,
        sources: Optional[List[str]] = None,
        limit: int = 20,
        offset: int = 0,
        sort_by: str = "date",  # "date" (latest last message first) or "rank" (BM25 score)
        fuzzy: bool = False,
    ) -> List[SearchResult]:
        """
        Execute FTS5 search over indexed session messages and titles.
        Returns matching messages with contextual snippets, sorted by date (latest last message) or BM25 rank.
        """
        if fuzzy:
            return self._fuzzy_search(query, sources, limit, offset)
        fts_query = sanitize_fts5_query(query)
        if not fts_query:
            return []

        with self._get_connection() as conn:
            sql = """
            SELECT
                s.session_id,
                s.message_id,
                s.source,
                s.title,
                s.role,
                s.content,
                bm25(search_index) AS score,
                snippet(search_index, 5, '«', '»', '...', 20) AS snippet,
                m.turn_index,
                COALESCE(m.timestamp, sess.updated_at) AS msg_timestamp,
                sess.updated_at AS session_updated_at
            FROM search_index s
            JOIN sessions sess ON s.session_id = sess.id
            LEFT JOIN messages m ON s.message_id = m.id
            WHERE search_index MATCH ?
            """
            params: List[Any] = [fts_query]

            if sources:
                placeholders = ",".join("?" for _ in sources)
                sql += f" AND s.source IN ({placeholders})"
                params.extend(sources)

            if sort_by == "rank":
                sql += " ORDER BY bm25(search_index) ASC, sess.updated_at DESC LIMIT ? OFFSET ?"
            else:
                # Default: most recent conversations first, then BM25 score
                sql += " ORDER BY sess.updated_at DESC, bm25(search_index) ASC LIMIT ? OFFSET ?"

            params.extend([limit, offset])

            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError as e:
                # If complex query syntax fails, fallback to simple escaped match
                safe_q = query.replace('"', '""')
                escaped_query = f'"{safe_q}"'
                fallback_params: List[Any] = [escaped_query]
                if sources:
                    fallback_params.extend(sources)
                fallback_params.extend([limit, offset])
                try:
                    rows = conn.execute(sql, fallback_params).fetchall()
                except Exception as fallback_err:
                    sys.stderr.write(
                        f"[aigator search error] Query '{query}' failed: {e}; fallback also failed: {fallback_err}\n"
                    )
                    return []

            results: List[SearchResult] = []
            for r in rows:
                results.append(
                    SearchResult(
                        session_id=r["session_id"],
                        message_id=r["message_id"],
                        source=r["source"],
                        title=r["title"],
                        role=r["role"],
                        snippet=r["snippet"] or (r["content"][:200] + "..."),
                        content=r["content"],
                        timestamp=r["msg_timestamp"] or "",
                        session_updated_at=r["session_updated_at"] or "",
                        score=float(r["score"]),
                        turn_index=r["turn_index"] or 0,
                    )
                )
            return results

    def _fuzzy_search(self, query, sources, limit, offset):
        """Literal subsequences, relevance first; oversized queries return no hits.

        SQL scans a normalized index to prefilter candidates. It is not an
        indexed-prefix lookup, but avoids shipping every full message into
        Python, bounds matching gaps, and never truncates by recency.
        """
        tokens = tokens_for(query)
        if not tokens or limit <= 0:
            return []
        offset = max(0, offset)
        sql = '''SELECT m.id AS message_id, m.session_id, m.role, m.content,
                        m.timestamp, m.turn_index, s.source, s.title, s.updated_at
                 FROM fuzzy_index f JOIN messages m ON m.id=f.message_id
                 JOIN sessions s ON s.id=m.session_id WHERE '''
        sql += ' AND '.join("f.text LIKE ? ESCAPE '\\'" for _ in tokens)
        params = [like_pattern(token) for token in tokens]
        if sources:
            sql += ' AND s.source IN (' + ','.join('?' for _ in sources) + ')'
            params.extend(sources)
        # Stable tie order before the bounded top-k heap; no full result sort.
        sql += ' ORDER BY s.updated_at DESC, m.id ASC'
        with self._get_connection() as conn:
            def matches():
                for row in conn.execute(sql, params):
                    match = score_match(row['title'], row['content'], tokens)
                    if match is not None:
                        yield match[0], row, match[1]
            best = heapq.nsmallest(limit + offset, matches(), key=lambda item: item[0])
        return [SearchResult(
            session_id=row['session_id'], message_id=row['message_id'], source=row['source'],
            title=row['title'], role=row['role'], content=row['content'],
            snippet=result_snippet(row['title'], row['content'], tokens, positions), timestamp=row['timestamp'],
            session_updated_at=row['updated_at'], score=float(score), turn_index=row['turn_index'],
        ) for score, row, positions in best[offset:]]

    def delete_session(self, session_id: str) -> bool:
        """Delete session and all its messages and index records."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM search_index WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM fuzzy_index WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            res = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()
            return res.rowcount > 0

    def get_stats(self) -> Dict[str, Any]:
        """Return aggregate statistics about the database."""
        with self._get_connection() as conn:
            total_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            total_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            
            source_counts = dict(
                conn.execute(
                    "SELECT source, COUNT(*) FROM sessions GROUP BY source"
                ).fetchall()
            )
            
            db_size_bytes = 0
            if not self.is_memory and self.db_path.exists():
                db_size_bytes = self.db_path.stat().st_size

            return {
                "db_path": str(self.db_path),
                "total_sessions": total_sessions,
                "total_messages": total_messages,
                "sessions_by_source": source_counts,
                "size_bytes": db_size_bytes,
                "size_mb": round(db_size_bytes / (1024 * 1024), 2),
            }
