"""Pruebas del servidor MCP (stdio y HTTP) con una base SQLite temporal.

Uso:  python3 -m unittest discover -s tests -v
Nunca toca la base real: SESSION_LOG_DB apunta a un directorio temporal.
"""
import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import mcp_server  # noqa: E402

TOKEN = "token-de-prueba"
OLD_SCHEMA = """
CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
  project TEXT NOT NULL DEFAULT 'general', summary TEXT NOT NULL, decisions TEXT NOT NULL DEFAULT '[]',
  pending TEXT NOT NULL DEFAULT '[]', tags TEXT NOT NULL DEFAULT '[]');
INSERT INTO sessions (created_at, project, summary, pending) VALUES ('2026-10-06T15:00:00Z', 'blog', 'Vieja', '["Publicar"]');
"""


def call(name, **arguments):
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    result = mcp_server.handle(msg)["result"]
    return result["content"][0]["text"], result["isError"]


class TempDBTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test.db")
        self._old_db = os.environ.get("SESSION_LOG_DB")
        os.environ["SESSION_LOG_DB"] = self.db

    def tearDown(self):
        if self._old_db is None:
            os.environ.pop("SESSION_LOG_DB", None)
        else:
            os.environ["SESSION_LOG_DB"] = self._old_db
        self.tmp.cleanup()


class ToolsTest(TempDBTestCase):
    def test_initialize_and_list(self):
        init = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": "2025-03-26"}})["result"]
        self.assertEqual(init["protocolVersion"], "2025-03-26")
        self.assertIn("Nunca la adivines", init["instructions"])
        tools = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        self.assertEqual({t["name"] for t in tools},
                         {"save_session", "query_sessions", "list_sessions_without_area", "set_area"})

    def test_area_required_and_validated(self):
        self.assertTrue(call("save_session", summary="x")[1])
        self.assertTrue(call("save_session", area="otra", summary="x")[1])
        self.assertTrue(call("save_session", area="trabajo", summary="  ")[1])

    def test_save_query_and_filters(self):
        self.assertEqual(call("save_session", area="trabajo", project="api", summary="Uno\nDos",
                              pending=["Backups"], tags=["devops"]), ("Guardado: 1", False))
        call("save_session", area="personal", project="blog", summary="Post")
        text, _ = call("query_sessions", last=1, area="trabajo")
        self.assertIn("api · trabajo", text)
        self.assertNotIn("blog", text)
        docs = json.loads(call("query_sessions", last=1, tag="devops", format="json")[0])
        self.assertEqual([d["project"] for d in docs], ["api"])
        self.assertEqual(call("query_sessions", last=1, area="personal", tag="devops")[0], "Sin resúmenes en ese periodo.")

    def test_summary_by_project(self):
        call("save_session", area="trabajo", project="api", summary="A", pending=["Backups"])
        call("save_session", area="trabajo", project="api", summary="B", pending=["Backups", "Monitoreo"])
        text, _ = call("query_sessions", last=1, summary_by="project")
        self.assertIn("# api (2 sesiones · trabajo)", text)
        self.assertEqual(text.count("[api] Backups"), 1)
        self.assertTrue(text.rstrip().endswith("[api] Monitoreo"))

    def test_bad_dates(self):
        self.assertTrue(call("query_sessions")[1])
        self.assertTrue(call("query_sessions", date="07-10-2026")[1])

    def test_migration_and_set_area(self):
        conn = sqlite3.connect(self.db)
        conn.executescript(OLD_SCHEMA)
        conn.close()
        self.assertIn("blog  Vieja", call("list_sessions_without_area")[0])
        self.assertIn("sin-area", call("query_sessions", date="2026-10-06", area="sin-area")[0])
        self.assertEqual(call("set_area", area="personal", project="blog")[0], "Actualizadas: 1")
        self.assertEqual(call("set_area", area="trabajo", ids=[1])[0], "Actualizadas: 0")  # sin force
        self.assertEqual(call("set_area", area="trabajo", ids=[1], force=True)[0], "Actualizadas: 1")
        text = call("query_sessions", date="2026-10-06")[0]
        self.assertIn("Publicar", text)  # los datos antiguos se conservan
        with self.assertRaises(sqlite3.IntegrityError):
            sqlite3.connect(self.db).execute("UPDATE sessions SET area = 'x'")

    def test_protocol_errors(self):
        self.assertIsNone(mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(mcp_server.parse_message("no-json")["error"]["code"], -32700)
        self.assertEqual(mcp_server.parse_message("[1]")["error"]["code"], -32600)
        unknown = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "x"}})
        self.assertEqual(unknown["error"]["code"], -32602)


class HTTPTest(TempDBTestCase):
    def setUp(self):
        super().setUp()
        self.server = mcp_server.make_http_server("127.0.0.1", 0, TOKEN, ["https://permitido.example"])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def request(self, method="POST", path="/mcp", body=None, token=TOKEN, headers=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            return e.code, None

    def test_tool_call_over_http(self):
        status, body = self.request(body={"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
            "name": "save_session", "arguments": {"area": "trabajo", "summary": "Por HTTP"}}})
        self.assertEqual((status, body["id"], body["result"]["isError"]), (200, 7, False))
        status, body = self.request(body={"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {
            "name": "query_sessions", "arguments": {"last": 1}}})
        self.assertIn("Por HTTP", body["result"]["content"][0]["text"])

    def test_auth(self):
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        self.assertEqual(self.request(body=ping, token=None)[0], 401)
        self.assertEqual(self.request(body=ping, token="malo")[0], 401)
        self.assertEqual(self.request(body=ping)[0], 200)

    def test_rejected_request_does_not_break_keepalive(self):
        # Regresión: un 401 sin leer el cuerpo dejaba basura en la conexión y la siguiente petición daba 400.
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1])
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        conn.request("POST", "/mcp", body, {"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        self.assertEqual(resp.status, 401)
        conn.request("POST", "/mcp", body, {"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
        resp = conn.getresponse()
        self.assertEqual((resp.status, json.loads(resp.read())["result"]), (200, {}))
        conn.close()

    def test_origin(self):
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        self.assertEqual(self.request(body=ping, headers={"Origin": "https://malo.example"})[0], 403)
        self.assertEqual(self.request(body=ping, headers={"Origin": "https://permitido.example"})[0], 200)

    def test_routes(self):
        self.assertEqual(self.request("GET", "/health", token=None), (200, {"status": "ok"}))
        self.assertEqual(self.request("GET", "/mcp")[0], 405)
        self.assertEqual(self.request(body={"jsonrpc": "2.0", "method": "ping"}, path="/otro")[0], 404)
        self.assertEqual(self.request(body={"jsonrpc": "2.0", "method": "notifications/initialized"})[0], 202)


if __name__ == "__main__":
    unittest.main()
