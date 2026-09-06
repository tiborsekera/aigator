"""Lightweight local HTTP ingestion daemon & Web UI server for Aigator."""

from __future__ import annotations

import json
import socket
import sqlite3
import os
import secrets
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

from aigator.db import Database, get_default_db_path
from aigator.parsers import detect_and_parse
from aigator.parsers.vscode_copilot import COPILOT_DB_DEFAULT, VSCodeCopilotParser
from aigator.server.web_ui import HTML_PAGE

MAX_INGEST_PAYLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
DEFAULT_AUTH_TOKEN_PATH = Path.home() / ".local" / "share" / "aigator" / "auth_token"


def get_or_create_auth_token() -> str:
    """Retrieve or generate persistent 0600 local auth token."""
    env_token = os.environ.get("AIGATOR_AUTH_TOKEN")
    if env_token:
        return env_token.strip()

    try:
        if DEFAULT_AUTH_TOKEN_PATH.exists():
            token = DEFAULT_AUTH_TOKEN_PATH.read_text(encoding="utf-8").strip()
            if token:
                return token

        DEFAULT_AUTH_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        new_token = secrets.token_hex(24)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(str(DEFAULT_AUTH_TOKEN_PATH), flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_token + "\n")
        return new_token
    except Exception:
        return secrets.token_hex(24)


class CopilotLiveWatcher(threading.Thread):
    """Watches VS Code Copilot session-store.db in the background and live-syncs turns."""

    def __init__(self, db: Database, interval_sec: float = 4.0) -> None:
        super().__init__(daemon=True, name="CopilotLiveWatcher")
        self.db = db
        self.interval = interval_sec
        self.last_version = None

    def sync_once(self, parser=None) -> None:
        if not COPILOT_DB_DEFAULT.exists():
            return
        def signature(path):
            try:
                stat = path.stat()
                return (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            except FileNotFoundError:
                return None
        version = tuple(signature(p) for p in (COPILOT_DB_DEFAULT, Path(str(COPILOT_DB_DEFAULT) + "-wal")))
        if version != self.last_version:
            sessions = (parser or VSCodeCopilotParser()).parse_db_file(COPILOT_DB_DEFAULT)
            for session in sessions:
                self.db.upsert_session(session)
            self.last_version = version

    def run(self) -> None:
        parser = VSCodeCopilotParser()
        while True:
            try:
                self.sync_once(parser)
            except Exception as e:
                sys.stderr.write(f"[CopilotLiveWatcher] Warning: {e}\n")
            time.sleep(self.interval)


class LocalHTTPServer(ThreadingHTTPServer):
    """Bound concurrent request workers; stalled sockets time out in the handler."""

    def __init__(self, *args, **kwargs):
        self._workers = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._workers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._workers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._workers.release()


class AigatorRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler with strict security controls and input validation."""

    db: Database
    auth_token: str = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def _validate_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True  # CLI, extension background, and userscript privileged requests
        try:
            parsed = urlparse(origin)
        except ValueError:
            return False
        if parsed.scheme == "chrome-extension" and parsed.hostname and len(parsed.hostname) == 32 and all(c in "abcdefghijklmnop" for c in parsed.hostname):
            return True  # Still requires explicit pairing/authentication for every API write.
        return origin == "http://" + self.headers.get("Host", "")

    def _validate_host_header(self) -> bool:
        """Protect against DNS rebinding attacks by validating Host header."""
        try:
            host_header = urlparse("//" + self.headers.get("Host", "")).hostname
        except ValueError:
            return False
        if not host_header:
            return False
        allowed = {"127.0.0.1", "localhost", "::1"}
        server_host = str(self.server.server_address[0]).lower()
        allowed.add(server_host)
        return host_header in allowed

    def _is_authorized(self) -> bool:
        """Check Bearer token, X-Aigator-Token header, or ?token= query parameter."""
        if not self.auth_token:
            return False

        # 1. Authorization: Bearer <token>
        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            bearer = auth_hdr[len("Bearer "):].strip()
            if secrets.compare_digest(bearer.encode(), self.auth_token.encode()):
                return True

        # 2. X-Aigator-Token: <token>
        custom_hdr = self.headers.get("X-Aigator-Token", "").strip()
        if custom_hdr and secrets.compare_digest(custom_hdr.encode(), self.auth_token.encode()):
            return True

        # 3. Query param token
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        q_token = q.get("token", [""])[0].strip()
        if q_token and secrets.compare_digest(q_token.encode(), self.auth_token.encode()):
            return True

        return False

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.end_headers()

    def _send_json(
        self, data: Any, status: int = HTTPStatus.OK
    ) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_content: str, status: int = HTTPStatus.OK) -> None:
        injected = html_content.replace('"__AIGATOR_AUTH_TOKEN__"', json.dumps(self.auth_token).replace("<", "\\u003c"))
        nonce = secrets.token_hex(24)
        injected = injected.replace("<script>", f'<script nonce="{nonce}">')
        body = injected.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(
        self, message: str, status: int = HTTPStatus.BAD_REQUEST
    ) -> None:
        self._send_json({"error": message, "status": "error"}, status=status)

    def do_GET(self) -> None:
        if not self._validate_host_header() or not self._validate_origin():
            self._send_error_json("Invalid Host or Origin header", HTTPStatus.BAD_REQUEST)
            return

        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._send_html(HTML_PAGE)
            return

        if path == "/api/auth/token":
            # Endpoint for local extension/client token auto-discovery (no CORS, protected by browser SOP)
            self._send_json({"token": self.auth_token, "status": "ok"})
            return

        # Check authentication for API endpoints
        if not self._is_authorized():
            self._send_error_json("Unauthorized: valid auth token required", HTTPStatus.UNAUTHORIZED)
            return

        if path == "/api/stats":
            stats = self.db.get_stats()
            self._send_json(stats)
            return

        if path == "/api/sessions":
            source = query.get("source", [None])[0]
            try:
                limit = max(1, min(int(query.get("limit", [50])[0]), 500))
            except (ValueError, TypeError):
                limit = 50
            try:
                offset = max(0, min(int(query.get("offset", [0])[0]), 1000000))
            except (ValueError, TypeError):
                offset = 0

            sessions = self.db.list_sessions(source=source, limit=limit, offset=offset)
            self._send_json(sessions)
            return

        if path.startswith("/api/sessions/"):
            session_id = unquote(path[len("/api/sessions/"):])
            session = self.db.get_session(session_id)
            if not session:
                self._send_error_json("Session not found", HTTPStatus.NOT_FOUND)
                return
            self._send_json(session.to_dict(include_messages=True))
            return

        if path == "/api/search":
            q = query.get("q", [""])[0]
            if not q:
                self._send_json([])
                return
            sources_val = query.get("source", [])
            sources = sources_val if sources_val and sources_val != [""] else None
            try:
                limit = max(1, min(int(query.get("limit", [20])[0]), 200))
            except (ValueError, TypeError):
                limit = 20
            try:
                offset = max(0, min(int(query.get("offset", [0])[0]), 1000000))
            except (ValueError, TypeError):
                offset = 0

            sort_by = query.get("sort", ["date"])[0]
            results = self.db.search(
                query=q,
                sources=sources,
                limit=limit,
                offset=offset,
                sort_by=sort_by,
                fuzzy=query.get("fuzzy", ["0"])[0].lower() in ("1", "true"),
            )
            self._send_json([r.to_dict() for r in results])
            return

        self._send_error_json("Endpoint not found", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self._validate_host_header() or not self._validate_origin():
            self._send_error_json(
                "Invalid Host or Origin header", HTTPStatus.BAD_REQUEST
            )
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/ingest":
            try:
                content_len = int(self.headers.get("Content-Length", 0))
            except (ValueError, TypeError):
                self._send_error_json("Invalid Content-Length", HTTPStatus.BAD_REQUEST)
                return

            if content_len <= 0:
                self._send_error_json("Empty request body", HTTPStatus.BAD_REQUEST)
                return

            if content_len > MAX_INGEST_PAYLOAD_BYTES:
                self._send_error_json(
                    f"Payload too large (max {MAX_INGEST_PAYLOAD_BYTES // (1024*1024)} MB)",
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                )
                return

            if not self._is_authorized():
                self._send_error_json("Unauthorized: valid auth token required", HTTPStatus.UNAUTHORIZED)
                return
            if self.headers.get_content_type() != "application/json":
                self._send_error_json("Content-Type must be application/json", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                return
            if self.headers.get("Transfer-Encoding"):
                self._send_error_json("Transfer-Encoding is unsupported")
                return

            try:
                body_bytes = self.rfile.read(content_len)
                if len(body_bytes) != content_len:
                    raise ValueError("Incomplete request body")
                payload = json.loads(body_bytes.decode("utf-8"))
                if not isinstance(payload, (dict, list)):
                    raise ValueError("Payload must be an object or array")
            except Exception as e:
                self._send_error_json(f"Invalid JSON payload: {e}", HTTPStatus.BAD_REQUEST)
                return

            query = parse_qs(parsed.query)
            source_hint = query.get("source", [None])[0]
            if not source_hint and isinstance(payload, dict):
                source_hint = payload.get("source")

            try:
                sessions = detect_and_parse(payload, source_hint=source_hint)
            except Exception as e:
                self._send_error_json(f"Failed to parse payload: {e}", HTTPStatus.BAD_REQUEST)
                return

            ingested_ids: List[str] = []
            if not sessions or not any(s.messages for s in sessions):
                self._send_error_json("No messages found in payload")
                return
            outcomes = []
            stored_message_count = 0
            try:
                for s in sessions:
                    status, _ = self.db.upsert_session(s)
                    outcomes.append(status)
                    ingested_ids.append(s.session_id)
                    stored_message_count += len(self.db.get_session(s.session_id).messages)
            except (sqlite3.Error, ValueError, TypeError) as exc:
                self._send_error_json(f"Ingestion failed: {exc}")
                return

            self._send_json(
                {
                    "status": "ok",
                    "count": len(sessions),
                    "stored_message_count": stored_message_count,
                    "outcomes": outcomes,
                    "session_ids": ingested_ids,
                    "first_title": sessions[0].title if sessions else None,
                },
            )
            return

        self._send_error_json("Endpoint not found", HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        if not self._validate_host_header() or not self._validate_origin():
            self._send_error_json("Invalid Host or Origin header", HTTPStatus.BAD_REQUEST)
            return

        if not self._is_authorized():
            self._send_error_json("Unauthorized", HTTPStatus.UNAUTHORIZED)
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/sessions/"):
            session_id = unquote(path[len("/api/sessions/"):])
            success = self.db.delete_session(session_id)
            if success:
                self._send_json({"status": "ok", "deleted": session_id})
            else:
                self._send_error_json("Session not found", HTTPStatus.NOT_FOUND)
            return

        self._send_error_json("Endpoint not found", HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default HTTP log spam."""
        pass


def run_daemon(
    host: str = "127.0.0.1",
    port: int = 8765,
    db_path: Optional[Path | str] = None,
    auth_token: Optional[str] = None,
) -> None:
    """Start local Aigator server."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Only loopback binding is supported")
    db = Database(db_path or get_default_db_path())
    token = auth_token or get_or_create_auth_token()

    class BoundHandler(AigatorRequestHandler):
        pass

    BoundHandler.db = db
    BoundHandler.auth_token = token

    class LocalServer(LocalHTTPServer):
        address_family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    server = LocalServer((host, port), BoundHandler)
    from aigator.parsers.cli_sessions import CliLiveWatcher
    copilot_watcher = CopilotLiveWatcher(db)
    copilot_watcher.start()
    cli_watcher = CliLiveWatcher(db)
    cli_watcher.start()
    print(f"🐊 Aigator Server running on http://{host}:{port}")
    print(f"   Database:        {db.db_path}")
    print(f"   Web UI:          http://{host}:{port}")
    print(f"   Auth Token:      {token[:6]}...{token[-4:]} (stored in ~/.local/share/aigator/auth_token)")
    print(f"   Copilot Live:    {'Active' if COPILOT_DB_DEFAULT.exists() else 'Waiting for VS Code session-store.db'}")
    print("   CLI Live:        Watching Claude Code, Codex and Copilot CLI session files")
    print("   Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Aigator Server...")
        server.server_close()
