"""API REST mínima + página única para la interfaz web. Reusa la lógica de sessions.py."""
import json
import re
from urllib.parse import parse_qs, urlparse

from common import ROOT
from sessions import (
    delete_session, format_results, query_sessions, resolve_range, update_session,
)

PAGE = (ROOT / "scripts" / "index.html").read_bytes()
CSS_DIR = ROOT / "scripts" / "css"


def css(name):
    """Contenido de css/<name> o None. Solo nombres planos *.css, sin rutas."""
    if not re.fullmatch(r"[\w.-]+\.css", name):
        return None
    f = CSS_DIR / name
    return f.read_bytes() if f.is_file() else None


def _range(q):
    one = lambda k: (q.get(k) or [None])[0]  # noqa: E731
    last = one("last")
    if not (one("date") or one("from") or one("to") or last):
        last = 7
    return resolve_range(one("date"), one("from"), one("to"), int(last) if last else None), one


def api(method, path, query, body):
    """-> (status, objeto JSON). ValueError = 400."""
    q = parse_qs(query)
    try:
        if method == "GET" and path == "/api/sessions":
            (start, end), one = _range(q)
            docs = query_sessions(start, end, one("project"), one("tag"), one("area"))
            return 200, json.loads(format_results(docs, as_json=True))
        if method == "GET" and path == "/api/report":
            (start, end), one = _range(q)
            docs = query_sessions(start, end, one("project"), one("tag"), one("area"))
            return 200, {"text": format_results(docs, summary_by="project")}
        sid = path.removeprefix("/api/sessions/")
        if path.startswith("/api/sessions/") and sid.isdigit():
            if method == "PUT":
                ok = update_session(int(sid), **(body or {}))
            elif method == "DELETE":
                ok = delete_session(int(sid))
            else:
                return 405, {"error": "método no permitido"}
            return (200, {"ok": True}) if ok else (404, {"error": "no existe"})
    except (ValueError, TypeError) as e:
        return 400, {"error": str(e)}
    return 404, {"error": "no encontrado"}
