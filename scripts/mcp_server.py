#!/usr/bin/env python3
"""Servidor MCP de session-log, solo con stdlib.

Implementa lo mínimo del protocolo: initialize, ping, tools/list y tools/call.
Dos transportes:
  - stdio: JSON-RPC delimitado por saltos de línea. Lo lanza el cliente MCP.
  - HTTP (--http): POST /mcp con un mensaje JSON-RPC y respuesta JSON
    ("Streamable HTTP" sin SSE). GET /health para chequeos de salud.

Uso:
  python3 scripts/mcp_server.py                      # stdio
  python3 scripts/mcp_server.py --http               # HTTP en MCP_HOST:MCP_PORT (127.0.0.1:8000)
Variables: MCP_AUTH_TOKEN (obligatoria si escucha fuera de localhost), MCP_HOST,
MCP_PORT, MCP_ALLOWED_ORIGINS (lista separada por comas), SESSION_LOG_DB, SESSION_LOG_TZ.
"""
import argparse
import hmac
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from common import AREAS, load_env
from sessions import (
    NO_AREA,
    format_results,
    query_sessions,
    resolve_range,
    save_session,
    sessions_without_area,
    set_area,
)

SERVER_INFO = {"name": "session-log", "version": "0.3.0"}
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MCP_PATH = "/mcp"
MAX_BODY = 1024 * 1024
LOOPBACK = ("127.0.0.1", "localhost", "::1")

INSTRUCTIONS = """\
session-log guarda resúmenes de sesiones que el usuario elige y los consulta por fecha.
- Guarda solo cuando el usuario lo pida; nunca automáticamente.
- area es obligatoria (trabajo o personal). Si el proyecto, la carpeta o la conversación no la dejan clara, pregúntale al usuario antes de guardar. Nunca la adivines.
- No incluyas secretos, tokens ni credenciales en los resúmenes.
- Las fechas son YYYY-MM-DD en hora de Panamá. Convierte "ayer", "esta semana" u "octubre" usando la fecha actual.
- Para standups o reportes usa query_sessions con summary_by="project".
- Si no hay resultados, dilo claramente; no inventes contenido."""

STR_LIST = {"type": "array", "items": {"type": "string"}}

TOOLS = [
    {
        "name": "save_session",
        "description": (
            "Guarda el resumen de la sesión actual. Úsala solo cuando el usuario lo pida. "
            "Si el área no es evidente, pregúntala antes de llamar a esta herramienta; nunca la adivines."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "area": {"type": "string", "enum": list(AREAS), "description": "trabajo o personal"},
                "project": {"type": "string", "description": "Nombre del proyecto, carpeta o repo. Por defecto 'general'."},
                "summary": {"type": "string", "description": "Resumen breve (3-8 líneas): qué se hizo, resultado y estado final."},
                "decisions": {**STR_LIST, "description": "Decisiones tomadas"},
                "pending": {**STR_LIST, "description": "Pendientes"},
                "tags": {**STR_LIST, "description": "1-3 tags"},
            },
            "required": ["area", "summary"],
        },
    },
    {
        "name": "query_sessions",
        "description": (
            "Consulta sesiones guardadas por día, rango o últimos N días (hora de Panamá). "
            "Indica date, from/to o last. Los filtros project, tag y area se combinan."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Un día: YYYY-MM-DD"},
                "from": {"type": "string", "description": "Inicio inclusive: YYYY-MM-DD"},
                "to": {"type": "string", "description": "Fin inclusive: YYYY-MM-DD"},
                "last": {"type": "integer", "minimum": 1, "description": "Últimos N días, incluye hoy"},
                "project": {"type": "string"},
                "tag": {"type": "string"},
                "area": {
                    "type": "string",
                    "enum": list(AREAS) + [NO_AREA],
                    "description": f"'{NO_AREA}' devuelve las sesiones antiguas que aún no tienen área",
                },
                "summary_by": {
                    "type": "string",
                    "enum": ["project"],
                    "description": "Consolidado agrupado por proyecto con los pendientes al final (standups, reportes)",
                },
                "format": {"type": "string", "enum": ["text", "json"], "description": "Por defecto text"},
            },
        },
    },
    {
        "name": "list_sessions_without_area",
        "description": "Lista las sesiones antiguas que no tienen área asignada.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_area",
        "description": (
            "Asigna el área a sesiones existentes por ids y/o proyecto. Pregunta al usuario el área; no la deduzcas. "
            "Sin force solo modifica sesiones que aún no tienen área."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "area": {"type": "string", "enum": list(AREAS)},
                "ids": {"type": "array", "items": {"type": "integer"}},
                "project": {"type": "string"},
                "force": {"type": "boolean", "description": "Sobrescribir áreas ya asignadas"},
            },
            "required": ["area"],
        },
    },
]
TOOL_NAMES = {t["name"] for t in TOOLS}


def call_tool(name, args):
    """Ejecuta una herramienta y devuelve el texto de respuesta. ValueError = error de uso."""
    if name == "save_session":
        session_id = save_session(
            args.get("area"),
            args.get("summary"),
            args.get("project") or "general",
            args.get("decisions") or [],
            args.get("pending") or [],
            args.get("tags") or [],
        )
        return f"Guardado: {session_id}"
    if name == "query_sessions":
        start, end = resolve_range(args.get("date"), args.get("from"), args.get("to"), args.get("last"))
        docs = query_sessions(start, end, args.get("project"), args.get("tag"), args.get("area"))
        return format_results(docs, args.get("summary_by"), args.get("format") == "json")
    if name == "list_sessions_without_area":
        return sessions_without_area()
    if name == "set_area":
        count = set_area(args.get("area"), args.get("ids") or [], args.get("project"), bool(args.get("force")))
        return f"Actualizadas: {count}"


def handle(msg):
    """Devuelve la respuesta JSON-RPC, o None para notificaciones."""
    method, msg_id = msg.get("method"), msg.get("id")
    if msg_id is None:
        return None  # notificación (p. ej. notifications/initialized)

    def result(value):
        return {"jsonrpc": "2.0", "id": msg_id, "result": value}

    def error(code, message):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    params = msg.get("params") or {}
    if method == "initialize":
        requested = params.get("protocolVersion")
        return result({
            "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return result({})
    if method == "tools/list":
        return result({"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        if name not in TOOL_NAMES:
            return error(-32602, f"Herramienta desconocida: {name}")
        try:
            text, is_error = call_tool(name, params.get("arguments") or {}), False
        except ValueError as e:
            text, is_error = f"Error: {e}", True
        except Exception:
            traceback.print_exc(file=sys.stderr)
            text, is_error = "Error interno; revisa los logs del servidor.", True
        return result({"content": [{"type": "text", "text": text}], "isError": is_error})
    return error(-32601, f"Método no soportado: {method}")


def parse_message(raw):
    """Texto JSON -> (respuesta JSON-RPC o None). Valida que sea un objeto."""
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON inválido"}}
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Se esperaba un objeto JSON-RPC"}}
    return handle(msg)


def serve_stdio():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        response = parse_message(line)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


class MCPHandler(BaseHTTPRequestHandler):
    server_version = "session-log-mcp"
    protocol_version = "HTTP/1.1"

    body_read = False

    def send(self, status, body=None, headers=()):
        data = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if self.command == "POST" and not self.body_read:
            # El cuerpo sin leer quedaría en la conexión keep-alive y corrompería la siguiente petición.
            self.send_header("Connection", "close")
            self.close_connection = True
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        token = self.server.auth_token
        if not token:
            return True
        supplied = self.headers.get("Authorization", "")
        return hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode())

    def origin_allowed(self):
        # Protección contra DNS rebinding: los clientes MCP no envían Origin; los navegadores sí.
        origin = self.headers.get("Origin")
        return origin is None or origin in self.server.allowed_origins

    def do_GET(self):
        if self.path == "/health":
            self.send(200, {"status": "ok"})
        elif self.path == MCP_PATH:
            self.send(405, {"error": "este servidor no ofrece stream SSE"}, [("Allow", "POST")])
        else:
            self.send(404, {"error": "no encontrado"})

    def do_DELETE(self):
        self.send(405, {"error": "sin sesiones"}, [("Allow", "POST")])

    def do_POST(self):
        if self.path != MCP_PATH:
            return self.send(404, {"error": "no encontrado"})
        if not self.origin_allowed():
            return self.send(403, {"error": "origen no permitido"})
        if not self.authorized():
            return self.send(401, {"error": "no autorizado"}, [("WWW-Authenticate", "Bearer")])
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.send(411, {"error": "falta Content-Length"})
        if length > MAX_BODY:
            return self.send(413, {"error": "cuerpo demasiado grande"})
        raw = self.rfile.read(length)
        self.body_read = True
        response = parse_message(raw)
        if response is None:
            return self.send(202)  # notificación: sin cuerpo
        status = 400 if response.get("id") is None and "error" in response else 200
        self.send(status, response)


def make_http_server(host, port, auth_token=None, allowed_origins=()):
    server = ThreadingHTTPServer((host, port), MCPHandler)
    server.daemon_threads = True
    server.auth_token = auth_token
    server.allowed_origins = set(allowed_origins)
    return server


def main():
    load_env()
    ap = argparse.ArgumentParser(description="Servidor MCP de session-log")
    ap.add_argument("--http", action="store_true", help="Servir por HTTP en vez de stdio")
    ap.add_argument("--host", default=os.environ.get("MCP_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "8000")))
    args = ap.parse_args()

    if not args.http:
        return serve_stdio()

    token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if not token and args.host not in LOOPBACK:
        sys.exit("Error: define MCP_AUTH_TOKEN para escuchar fuera de localhost.")
    origins = [o.strip() for o in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    server = make_http_server(args.host, args.port, token or None, origins)
    print(f"session-log MCP en http://{args.host}:{args.port}{MCP_PATH}", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
