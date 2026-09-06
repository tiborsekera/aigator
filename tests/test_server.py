"""Integration tests for Aigator HTTP Daemon and REST API."""

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from aigator.db import Database
from aigator.server.daemon import AigatorRequestHandler, LocalHTTPServer


class TestDaemonServer(unittest.TestCase):
    server: ThreadingHTTPServer
    server_thread: threading.Thread
    port: int = 8991
    auth_token: str = "test-secret-token-12345"
    db: Database

    @classmethod
    def setUpClass(cls):
        cls.db = Database(":memory:")
        
        class TestBoundHandler(AigatorRequestHandler):
            db = cls.db
            auth_token = cls.auth_token

        cls.server = LocalHTTPServer(("127.0.0.1", 0), TestBoundHandler)
        cls.port = cls.server.server_port
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _request(self, path: str, method: str = "GET", data: dict = None, token: str = None, headers: dict = None):
        url = f"http://127.0.0.1:{self.port}{path}"
        req_headers = headers or {}
        if token is not None:
            if token:
                req_headers["Authorization"] = f"Bearer {token}"
        elif self.auth_token:
            req_headers["Authorization"] = f"Bearer {self.auth_token}"

        body = json.dumps(data).encode("utf-8") if data is not None else None
        if body:
            req_headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode("utf-8")
                try:
                    return resp.status, json.loads(raw), resp.headers
                except Exception:
                    return resp.status, raw, resp.headers
        except urllib.error.HTTPError as e:
            err_raw = e.read().decode("utf-8")
            try:
                return e.code, json.loads(err_raw), e.headers
            except Exception:
                return e.code, err_raw, e.headers

    def test_mutation_auth_required(self):
        payload = {"source": "perplexity", "id": "auth-regression", "turns": [{"author": "user", "text": "test"}]}
        for token in ("", "wrong"):
            self.assertEqual(self._request("/api/ingest", method="POST", data=payload, token=token)[0], 401)
            self.assertEqual(self._request("/api/sessions/missing", method="DELETE", token=token)[0], 401)
        status, result, headers = self._request("/api/ingest", method="POST", data=payload)
        self.assertEqual(status, 200)
        self.assertEqual(result["stored_message_count"], 1)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self._request("/api/sessions/" + result["session_ids"][0], method="DELETE")[0], 200)

    def test_cross_origin_requests_rejected(self):
        for path in ("/", "/api/auth/token", "/api/stats"):
            self.assertEqual(self._request(path, headers={"Origin": "https://evil.example"})[0], 400)
        self.assertEqual(self._request("/api/ingest", method="POST", data={}, headers={"Origin": "https://evil.example"})[0], 400)

    def test_invalid_payloads(self):
        self.assertEqual(self._request("/api/ingest", method="POST", data=42)[0], 400)
        self.assertEqual(self._request("/api/ingest", method="POST", data={"source": "perplexity", "turns": []})[0], 400)
        self.assertEqual(self._request("/api/ingest", method="POST", headers={"Content-Length": "-1"})[0], 400)
        self.assertEqual(self._request("/api/ingest", method="POST", headers={"Content-Length": "1", "Content-Type": "text/plain"})[0], 415)

    def test_web_ui_html_served(self):
        status, html, _ = self._request("/", token="")
        self.assertEqual(status, 200)
        self.assertIn("<title>Aigator", html)
        self.assertIn(self.auth_token, html)

    def test_auth_token_discovery(self):
        status, res, _ = self._request("/api/auth/token", token="")
        self.assertEqual(status, 200)
        self.assertEqual(res.get("token"), self.auth_token)

    def test_unauthorized_access_rejected(self):
        status, res, _ = self._request("/api/stats", token="invalid-token")
        self.assertEqual(status, 401)
        self.assertEqual(res.get("status"), "error")

    def test_stats_endpoint_authorized(self):
        status, res, _ = self._request("/api/stats")
        self.assertEqual(status, 200)
        self.assertIn("total_sessions", res)

    def test_ingest_and_search_workflow(self):
        payload = {
            "source": "chatgpt",
            "id": "daemon-test-1",
            "title": "Async Rust Testing",
            "turns": [
                {"author": "user", "text": "How do I write unit tests for async Rust code?"},
                {"author": "assistant", "text": "Use the #[tokio::test] macro from Tokio."}
            ]
        }
        status, res, _ = self._request("/api/ingest", method="POST", data=payload)
        self.assertEqual(status, 200)
        self.assertEqual(res.get("status"), "ok")
        self.assertEqual(res.get("count"), 1)

        # Search
        status, search_res, _ = self._request("/api/search?q=tokio")
        self.assertEqual(status, 200)
        self.assertEqual(len(search_res), 1)
        self.assertEqual(search_res[0]["title"], "Async Rust Testing")

        # Invalid numeric input doesn't crash daemon
        status, sessions, _ = self._request("/api/sessions?limit=invalid_number&offset=bad")
        self.assertEqual(status, 200)
        self.assertIsInstance(sessions, list)


if __name__ == "__main__":
    unittest.main()
